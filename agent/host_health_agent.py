#!/usr/bin/env python3
# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Push agent for alfaEdge Pulse's Host Health module.

Deliberately plain standard-library Python — this runs standalone on each
monitored guest under a systemd timer (see host-health-agent.service/
.timer in this same directory), with no guarantee it's ever invoked from
inside this app's own virtualenv, so it can't import `frappe`,
`requests`, or anything else that isn't already on a bare system Python.
Runs as the bench's own OS user; nothing here needs root.

Each cycle:
  1. Probes the OS services listed in the local config via `systemctl show`
     (unprivileged, read-only D-Bus property query).
  2. If `bench_path`/`site_name` are configured, shells out to
     `bench --site <site_name> execute
     pulse_agent.agent_collect.collect_bench_health` to get RQ worker/queue/
     failed-job/scheduler data from inside a real Frappe context (see that
     function's own docstring for why this is a subprocess call and not a
     direct import), and separately walks `bench_path/sites/*` to inventory
     every site on the bench (not just `site_name` — see the module's own
     note on why one site is enough for the RQ checks but not for
     inventory) via `site_config.json`'s `host_name`, when set.
  3. POSTs everything in one payload to the ingest endpoint using this
     host's own `api_key`/`api_secret` (see the central Pulse site's
     monitored_host.py — every Monitored Host has its own independent pair
     for exactly this purpose).

Requires the target bench to have `pulse_agent` itself available: `bench
get-app <this repo's URL>` followed by `bench --site <site_name>
install-app pulse_agent` — `bench execute` checks the target site's
installed-apps list (`AppNotInstalledError` otherwise), not just whether
the module is importable, even though this app defines no doctypes of its
own. Deliberately its own minimal app rather than living inside the
main `alfaedge_pulse` app: the guest fleet runs a mix of Frappe/Python
versions, and `alfaedge_pulse` pins to whatever Python the central Pulse
site itself happens to run — installing the *whole* dashboard app (and its
version pin) onto every monitored guest just to run this one function was
never the right shape.

Config file is JSON — see config.example.json in this directory. Path
defaults to /etc/host-health-agent/config.json, override with --config.

`bench_path`/`site_name`/`services` are all optional: a guest that runs no
Frappe bench at all can have `bench_path` unset and will simply omit
`benches`/`sites` from its payload every cycle (see ingest.py's push_status
docstring — omitting a field, not sending an empty list, is what means "not
applicable" there).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_CONFIG_PATH = "/etc/host-health-agent/config.json"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_BENCH_EXECUTE_TIMEOUT_SECONDS = 20
KNOWN_STATES = {"active", "inactive", "failed", "activating", "deactivating"}
SKIP_SITE_DIRS = {"assets"}

logging.basicConfig(
	level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("host-health-agent")


def load_config(path: str) -> dict:
	with open(path) as f:
		config = json.load(f)
	for required in ("ingest_url", "api_key", "api_secret"):
		if not config.get(required):
			raise ValueError(f"config is missing required field: {required}")
	return config


def check_services(services: list[dict]) -> list[dict]:
	results = []
	for svc in services:
		unit_name = svc.get("unit_name")
		service_name = svc.get("service_name") or unit_name
		if not unit_name:
			log.warning("skipping service entry with no unit_name: %r", svc)
			continue
		state = _systemctl_active_state(unit_name)
		results.append({"service_name": service_name, "unit_name": unit_name, "current_state": state})
	return results


def _systemctl_active_state(unit_name: str) -> str:
	try:
		proc = subprocess.run(
			["systemctl", "show", unit_name, "--property=ActiveState", "--value"],
			capture_output=True,
			text=True,
			timeout=5,
		)
	except (OSError, subprocess.TimeoutExpired) as e:
		log.warning("systemctl check failed for %s: %s", unit_name, e)
		return "unknown"
	state = proc.stdout.strip()
	# systemctl's own vocabulary is a superset of ours (e.g. "reloading") —
	# fold anything we don't have a bucket for into "unknown" rather than
	# guessing, matching ingest.py's own server-side fallback.
	return state if state in KNOWN_STATES else "unknown"


def discover_sites(bench_path: str) -> list[dict]:
	sites_dir = os.path.join(bench_path, "sites")
	bench_name = os.path.basename(os.path.normpath(bench_path))
	rows = []
	if not os.path.isdir(sites_dir):
		return rows
	for entry in sorted(os.listdir(sites_dir)):
		if entry in SKIP_SITE_DIRS or entry.startswith("."):
			continue
		site_dir = os.path.join(sites_dir, entry)
		config_path = os.path.join(site_dir, "site_config.json")
		if not os.path.isdir(site_dir) or not os.path.isfile(config_path):
			continue
		site_url = None
		try:
			with open(config_path) as f:
				site_config = json.load(f)
			site_url = site_config.get("host_name") or None
		except (OSError, ValueError) as e:
			log.warning("could not read site_config.json for %s: %s", entry, e)
		rows.append({"bench_name": bench_name, "site_name": entry, "site_url": site_url})
	return rows


def collect_bench_health(bench_path: str, site_name: str) -> dict | None:
	bench_name = os.path.basename(os.path.normpath(bench_path))
	try:
		proc = subprocess.run(
			["bench", "--site", site_name, "execute", "pulse_agent.agent_collect.collect_bench_health"],
			cwd=bench_path,
			capture_output=True,
			text=True,
			timeout=DEFAULT_BENCH_EXECUTE_TIMEOUT_SECONDS,
		)
	except (OSError, subprocess.TimeoutExpired) as e:
		log.warning("bench execute failed for %s: %s", bench_name, e)
		return None
	if proc.returncode != 0:
		log.warning("bench execute exited %s for %s: %s", proc.returncode, bench_name, proc.stderr.strip()[-2000:])
		return None
	try:
		data = json.loads(proc.stdout.strip())
	except ValueError as e:
		log.warning("bench execute returned non-JSON output for %s: %s", bench_name, e)
		return None
	data["bench_name"] = bench_name
	return data


def build_payload(config: dict) -> dict:
	payload = {"reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

	services = config.get("services") or []
	if services:
		payload["services"] = check_services(services)

	bench_path = config.get("bench_path")
	site_name = config.get("site_name")
	if bench_path:
		sites = discover_sites(bench_path)
		if sites:
			payload["sites"] = sites
		if site_name:
			bench_health = collect_bench_health(bench_path, site_name)
			if bench_health is not None:
				payload["benches"] = [bench_health]

	return payload


def push(config: dict, payload: dict) -> None:
	body = json.dumps(payload).encode("utf-8")
	req = urllib.request.Request(
		config["ingest_url"],
		data=body,
		method="POST",
		headers={
			"Content-Type": "application/json",
			"Authorization": f"token {config['api_key']}:{config['api_secret']}",
			"Frappe-Authorization-Source": "Monitored Host",
		},
	)
	timeout = config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			result = json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as e:
		detail = e.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"ingest endpoint rejected push: HTTP {e.code}: {detail[:500]}") from e
	except urllib.error.URLError as e:
		raise RuntimeError(f"could not reach ingest endpoint: {e.reason}") from e

	message = result.get("message", result)
	warnings = message.get("warnings") if isinstance(message, dict) else None
	if warnings:
		for w in warnings:
			log.warning("server-reported issue: %s", w)
	log.info("push ok: %s", message)


def main() -> int:
	parser = argparse.ArgumentParser(description="alfaEdge Pulse Host Health push agent")
	parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
	args = parser.parse_args()

	try:
		config = load_config(args.config)
		payload = build_payload(config)
		push(config, payload)
	except Exception as e:
		log.error("push cycle failed: %s", e)
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
