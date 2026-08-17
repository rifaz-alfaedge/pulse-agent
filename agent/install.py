#!/usr/bin/env python3
# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Interactive installer for the alfaEdge Pulse Host Health push agent —
and, optionally right after, the separate Resource & Capacity Monitoring
collector (resource_agent.py, its own process/config/systemd units, see
install_resource_agent() below).

Replaces the manual "mkdir, cp, hand-edit config.json, hand-edit the
systemd unit, daemon-reload, enable" flow documented in this app's own
README — every one of those steps has caused a real mistake across
several live installs (a stale `ExecStart` path, a missing URL scheme on
`ingest_url`, a forgotten `bench --site X install-app pulse_agent`, an
un-edited `User=` placeholder). This script prompts for the handful of
values that are genuinely per-host, auto-detects what it safely can, and
— critically — runs one real test push *before* enabling the systemd
timer, so a bad URL/key/bench/site surfaces immediately with a clear
error instead of silently retrying every ~25s under `journalctl`.

Two things deliberately stay manual, out of scope here:
  - Creating the Monitored Host record and generating its api_key/
    api_secret happens once in Desk, on the *central* Pulse site — this
    script only ever consumes an already-generated key pair.
  - `bench get-app`/`bench --site <site> install-app pulse_agent` — this
    script assumes the app is already installed on this guest's bench; it
    only checks and warns if it isn't, never runs install-app itself.

Privilege model: this script runs as a normal user throughout. Only the
individual steps that actually need root (writing to /opt, /etc,
`systemctl`) shell out to `sudo` one at a time via `run_sudo()` below —
there's no upfront root check and no requirement to invoke this whole
script with `sudo python3 install.py`.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
INSTALL_DIR = Path("/opt/host-health-agent")
CONFIG_DIR = Path("/etc/host-health-agent")
CONFIG_PATH = CONFIG_DIR / "config.json"
SCRIPT_DEST = INSTALL_DIR / "host_health_agent.py"
SERVICE_DEST = Path("/etc/systemd/system/host-health-agent.service")
TIMER_DEST = Path("/etc/systemd/system/host-health-agent.timer")

INGEST_PATH_SUFFIX = "/api/method/alfaedge_pulse.host_health.ingest.push_status"

# Resource & Capacity Monitoring is a separate, optional add-on collector
# (own process, own cadence) — see install_resource_agent() below. Kept as
# its own script/config/systemd-unit set, same reasoning as
# resource_agent.py's own docstring: it has zero Frappe/bench dependency,
# so bundling it onto host-health-agent's 25s cadence would slow that down
# for no reason.
RESOURCE_INSTALL_DIR = Path("/opt/resource-agent")
RESOURCE_CONFIG_DIR = Path("/etc/resource-agent")
RESOURCE_CONFIG_PATH = RESOURCE_CONFIG_DIR / "config.json"
RESOURCE_SCRIPT_DEST = RESOURCE_INSTALL_DIR / "resource_agent.py"
RESOURCE_SERVICE_DEST = Path("/etc/systemd/system/resource-agent.service")
RESOURCE_TIMER_DEST = Path("/etc/systemd/system/resource-agent.timer")

RESOURCE_INGEST_PATH_SUFFIX = "/api/method/alfaedge_pulse.host_health.ingest.push_resource_metrics"

# Order within each list is the preference when more than one candidate is
# actually present (Debian/Ubuntu-first naming).
KNOWN_SERVICES = {
	"SSH": ["ssh.service", "sshd.service"],
	"nginx": ["nginx.service"],
	"MariaDB": ["mariadb.service", "mysql.service", "mysqld.service"],
	"Redis": ["redis-server.service", "redis.service"],
}


def prompt(label: str, default: str | None = None) -> str:
	suffix = f" [{default}]" if default else ""
	while True:
		value = input(f"{label}{suffix}: ").strip()
		if value:
			return value
		if default is not None:
			return default
		print("This value is required.")


def prompt_yes_no(label: str, default: bool = True) -> bool:
	suffix = " [Y/n]" if default else " [y/N]"
	while True:
		value = input(f"{label}{suffix}: ").strip().lower()
		if not value:
			return default
		if value in ("y", "yes"):
			return True
		if value in ("n", "no"):
			return False
		print("Please answer y or n.")


def run_sudo(args: list[str]) -> None:
	"""Runs one privileged command via sudo. Interactive — sudo's own
	password prompt (if needed) goes straight to the terminal, not
	captured. Raises on failure so callers don't have to check returncode
	themselves."""
	subprocess.run(["sudo", *args], check=True)


def get_os_user() -> str:
	default = None
	try:
		default = getpass.getuser()
	except Exception:
		try:
			default = pwd.getpwuid(os.getuid()).pw_name
		except Exception:
			default = None
	while True:
		user = prompt("OS user this agent will run as", default)
		try:
			pwd.getpwnam(user)
			return user
		except KeyError:
			print(f"No such OS user: {user!r}")


def discover_site_names(bench_path: Path) -> list[str]:
	"""Same filter host_health_agent.discover_sites() itself uses — every
	directory under sites/ with its own site_config.json, skipping
	assets/dotfiles."""
	sites_dir = bench_path / "sites"
	if not sites_dir.is_dir():
		return []
	names = []
	for entry in sorted(sites_dir.iterdir()):
		if entry.name.startswith(".") or entry.name == "assets":
			continue
		if entry.is_dir() and (entry / "site_config.json").is_file():
			names.append(entry.name)
	return names


def check_pulse_agent_installed(user: str, bench_path: Path, site_name: str) -> None:
	"""Best-effort, warn-not-block sanity check for the single most common
	mistake seen live this session: running `bench get-app` and assuming
	that alone means the app is usable. Does not run install-app itself —
	confirmed out of scope."""
	try:
		result = subprocess.run(
			["sudo", "-u", user, "bench", "--site", site_name, "list-apps"],
			cwd=bench_path,
			capture_output=True,
			text=True,
			timeout=30,
		)
	except (OSError, subprocess.TimeoutExpired) as e:
		print(f"(could not check installed apps: {e} — continuing anyway)")
		return
	if "pulse_agent" not in result.stdout:
		print(
			"WARNING: 'pulse_agent' doesn't appear in this site's installed apps.\n"
			f"  Run this first:  bench --site {site_name} install-app pulse_agent\n"
			"  ...otherwise bench-health collection will fail every cycle."
		)


def get_bench_info(user: str) -> tuple[str | None, str | None]:
	if not prompt_yes_no("Does this guest run a Frappe bench?", default=True):
		return None, None

	default_bench = f"/home/{user}/frappe-bench"
	while True:
		bench_path = Path(prompt("Bench path", default_bench))
		if (bench_path / "sites").is_dir():
			break
		print(f"{bench_path} doesn't look like a bench (no sites/ directory found).")
		if prompt_yes_no("Use it anyway?", default=False):
			break

	sites = discover_site_names(bench_path)
	if not sites:
		print("No sites with a site_config.json found — enter one manually.")
		site_name = prompt("Site name")
	elif len(sites) == 1:
		site_name = sites[0]
		print(f"Found one site: {site_name}")
	else:
		print("Multiple sites found:")
		for i, s in enumerate(sites, 1):
			print(f"  {i}. {s}")
		while True:
			choice = prompt(f"Pick a site [1-{len(sites)}]", "1")
			if choice.isdigit() and 1 <= int(choice) <= len(sites):
				site_name = sites[int(choice) - 1]
				break
			print("Invalid choice.")

	check_pulse_agent_installed(user, bench_path, site_name)
	return str(bench_path), site_name


def build_ingest_url(domain: str, use_https: bool, suffix: str) -> str:
	scheme = "https" if use_https else "http"
	return f"{scheme}://{domain}{suffix}"


def prompt_domain_and_scheme() -> tuple[str, bool]:
	"""One prompt, reused to build both Host Health's and the optional
	Resource agent's ingest URL (see build_ingest_url) — same dashboard
	domain either way, so there's no reason to ask twice."""
	while True:
		domain = prompt("Pulse dashboard domain (e.g. pulse.example.com — not the full URL)")
		if "://" in domain or "/" in domain:
			domain = domain.split("://")[-1].split("/")[0]
			print(f"Only the domain is needed — using: {domain}")
		if domain:
			break
	use_https = prompt_yes_no("Use https?", default=True)
	if not use_https:
		print("WARNING: http transmits this agent's api_key/api_secret unencrypted.")
	return domain, use_https


def _print_service_table(rows: list[dict]) -> None:
	print("\nDetected services (edit before continuing):")
	for row in rows:
		mark = "x" if row["unit_name"] else " "
		shown = row["unit_name"] or "not found"
		print(f"  [{mark}] {row['service_name']:<9}-> {shown}")


def detect_services() -> list[dict]:
	try:
		result = subprocess.run(
			["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
			capture_output=True,
			text=True,
			timeout=10,
		)
		available = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
	except (OSError, subprocess.TimeoutExpired):
		available = set()

	detected = [
		{"service_name": name, "unit_name": next((c for c in candidates if c in available), None)}
		for name, candidates in KNOWN_SERVICES.items()
	]

	_print_service_table(detected)
	while True:
		choice = prompt("[enter] accept, [e] edit, [a] add custom, [r] remove", "")
		if choice == "":
			break
		if choice == "e":
			for row in detected:
				new_unit = prompt(f"  {row['service_name']} unit name", row["unit_name"] or "(skip)")
				row["unit_name"] = None if new_unit == "(skip)" else new_unit
		elif choice == "a":
			detected.append({"service_name": prompt("  Service name (label)"), "unit_name": prompt("  Unit name")})
		elif choice == "r":
			target = prompt("  Service name to remove")
			detected = [r for r in detected if r["service_name"] != target]
		else:
			print("Unrecognized option.")
		_print_service_table(detected)

	return [{"service_name": r["service_name"], "unit_name": r["unit_name"]} for r in detected if r["unit_name"]]


def build_config(
	ingest_url: str, api_key: str, api_secret: str, bench_path: str | None, site_name: str | None, services: list[dict]
) -> dict:
	config: dict = {"ingest_url": ingest_url, "api_key": api_key, "api_secret": api_secret}
	if bench_path:
		config["bench_path"] = bench_path
	if site_name:
		config["site_name"] = site_name
	if services:
		config["services"] = services
	config["timeout_seconds"] = 10
	return config


def existing_install_detected() -> bool:
	return any(p.exists() for p in (CONFIG_PATH, SCRIPT_DEST, SERVICE_DEST, TIMER_DEST))


def stop_timer_if_active() -> None:
	result = subprocess.run(["systemctl", "is-active", "--quiet", "host-health-agent.timer"])
	if result.returncode == 0:
		run_sudo(["systemctl", "stop", "host-health-agent.timer"])


def resource_build_config(ingest_url: str, api_key: str, api_secret: str) -> dict:
	"""No bench_path/site_name/services — resource_agent.py has zero
	Frappe/bench dependency, unlike host_health_agent.py's config."""
	return {"ingest_url": ingest_url, "api_key": api_key, "api_secret": api_secret, "timeout_seconds": 10}


def resource_existing_install_detected() -> bool:
	return any(p.exists() for p in (RESOURCE_CONFIG_PATH, RESOURCE_SCRIPT_DEST, RESOURCE_SERVICE_DEST, RESOURCE_TIMER_DEST))


def resource_stop_timer_if_active() -> None:
	result = subprocess.run(["systemctl", "is-active", "--quiet", "resource-agent.timer"])
	if result.returncode == 0:
		run_sudo(["systemctl", "stop", "resource-agent.timer"])


def resource_write_config(config: dict, user: str) -> None:
	with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
		json.dump(config, f, indent="\t")
		f.write("\n")
		tmp_path = f.name
	try:
		run_sudo(["install", "-m", "600", "-o", user, "-g", user, tmp_path, str(RESOURCE_CONFIG_PATH)])
	finally:
		os.unlink(tmp_path)


def resource_write_agent_script() -> None:
	src = AGENT_DIR / "resource_agent.py"
	run_sudo(["install", "-m", "755", str(src), str(RESOURCE_SCRIPT_DEST)])


def resource_write_systemd_units(user: str) -> None:
	template = (AGENT_DIR / "resource-agent.service").read_text()
	rendered = template.replace("__PULSE_AGENT_USER__", user)
	with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False) as f:
		f.write(rendered)
		tmp_service = f.name
	try:
		run_sudo(["install", "-m", "644", tmp_service, str(RESOURCE_SERVICE_DEST)])
	finally:
		os.unlink(tmp_service)
	run_sudo(["install", "-m", "644", str(AGENT_DIR / "resource-agent.timer"), str(RESOURCE_TIMER_DEST)])


def resource_test_invocation(user: str) -> bool:
	print("\nRunning a test push (Resource & Capacity Monitoring)...\n")
	result = subprocess.run(
		["sudo", "-u", user, "/usr/bin/python3", str(RESOURCE_SCRIPT_DEST), "--config", str(RESOURCE_CONFIG_PATH)],
		capture_output=True,
		text=True,
	)
	if result.stdout:
		print(result.stdout, end="")
	if result.stderr:
		print(result.stderr, end="", file=sys.stderr)
	return result.returncode == 0


def install_resource_agent(user: str, api_key: str, api_secret: str, domain: str, use_https: bool) -> None:
	"""Optional add-on, offered after Host Health is fully installed and
	enabled — reuses the already-entered user/api_key/api_secret/domain so
	nothing has to be re-typed. Independent of the Host Health flow above:
	a failure here (bad test push, systemd error) is reported and left for
	the admin to retry, but never rolls back or blocks the already-working
	Host Health installation."""
	if not prompt_yes_no("\nAlso install the Resource & Capacity Monitoring collector?", default=True):
		return

	ingest_url = build_ingest_url(domain, use_https, RESOURCE_INGEST_PATH_SUFFIX)
	config = resource_build_config(ingest_url, api_key, api_secret)
	print(f"\nResource agent ingest URL: {ingest_url}")

	if resource_existing_install_detected():
		if not prompt_yes_no("Existing Resource agent installation detected. Overwrite?", default=False):
			print("Skipped — existing Resource agent installation left untouched.")
			return
		resource_stop_timer_if_active()

	try:
		run_sudo(["mkdir", "-p", str(RESOURCE_INSTALL_DIR), str(RESOURCE_CONFIG_DIR)])
		resource_write_agent_script()
		resource_write_config(config, user)
		resource_write_systemd_units(user)
		run_sudo(["systemctl", "daemon-reload"])
	except subprocess.CalledProcessError as e:
		print(f"\nResource agent install failed: {e}", file=sys.stderr)
		return

	if not resource_test_invocation(user):
		print(
			f"\nResource agent test push failed — its timer was NOT enabled. Fix the issue above, then re-run:\n"
			f"  sudo -u {user} /usr/bin/python3 {RESOURCE_SCRIPT_DEST} --config {RESOURCE_CONFIG_PATH}"
		)
		return

	try:
		run_sudo(["systemctl", "enable", "--now", "resource-agent.timer"])
	except subprocess.CalledProcessError as e:
		print(f"\nResource agent test push succeeded, but enabling the timer failed: {e}", file=sys.stderr)
		return

	print(
		"\nResource & Capacity Monitoring agent installed — pushing every ~3 minutes.\n"
		"Watch it:  sudo journalctl -u resource-agent.service -f"
	)


def write_config(config: dict, user: str) -> None:
	with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
		json.dump(config, f, indent="\t")
		f.write("\n")
		tmp_path = f.name
	try:
		run_sudo(["install", "-m", "600", "-o", user, "-g", user, tmp_path, str(CONFIG_PATH)])
	finally:
		os.unlink(tmp_path)


def write_agent_script() -> None:
	src = AGENT_DIR / "host_health_agent.py"
	run_sudo(["install", "-m", "755", str(src), str(SCRIPT_DEST)])


def write_systemd_units(user: str) -> None:
	template = (AGENT_DIR / "host-health-agent.service").read_text()
	rendered = template.replace("__PULSE_AGENT_USER__", user)
	with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False) as f:
		f.write(rendered)
		tmp_service = f.name
	try:
		run_sudo(["install", "-m", "644", tmp_service, str(SERVICE_DEST)])
	finally:
		os.unlink(tmp_service)
	run_sudo(["install", "-m", "644", str(AGENT_DIR / "host-health-agent.timer"), str(TIMER_DEST)])


def test_invocation(user: str) -> bool:
	print("\nRunning a test push...\n")
	result = subprocess.run(
		["sudo", "-u", user, "/usr/bin/python3", str(SCRIPT_DEST), "--config", str(CONFIG_PATH)],
		capture_output=True,
		text=True,
	)
	if result.stdout:
		print(result.stdout, end="")
	if result.stderr:
		print(result.stderr, end="", file=sys.stderr)
	return result.returncode == 0


def print_summary(
	user: str,
	bench_path: str | None,
	site_name: str | None,
	ingest_url: str,
	api_key: str,
	api_secret: str,
	services: list[dict],
) -> None:
	print("\n--- Summary ---")
	print(f"OS user:     {user}")
	print(f"Bench path:  {bench_path or '(none — no bench on this guest)'}")
	print(f"Site name:   {site_name or '(none)'}")
	print(f"Ingest URL:  {ingest_url}")
	print(f"API key:     {api_key}")
	print(f"API secret:  {'*' * len(api_secret)}")
	print(f"Services:    {', '.join(r['service_name'] for r in services) or '(none)'}")
	print("\nWill write:")
	print(f"  {SCRIPT_DEST}")
	print(f"  {CONFIG_PATH}  (mode 600, owner {user})")
	print(f"  {SERVICE_DEST}")
	print(f"  {TIMER_DEST}")


def main() -> int:
	parser = argparse.ArgumentParser(description="Interactive installer for the Host Health push agent")
	parser.add_argument("-y", "--force", action="store_true", help="skip confirmation prompts")
	parser.add_argument("--dry-run", action="store_true", help="print what would happen, write nothing")
	args = parser.parse_args()

	print("alfaEdge Pulse — Host Health agent installer\n")

	user = get_os_user()
	bench_path, site_name = get_bench_info(user)
	domain, use_https = prompt_domain_and_scheme()
	ingest_url = build_ingest_url(domain, use_https, INGEST_PATH_SUFFIX)
	api_key = prompt("API key (from the Monitored Host record's 'Generate Agent Key' button)")
	api_secret = getpass.getpass("API secret (hidden): ")
	while not api_secret:
		print("API secret is required.")
		api_secret = getpass.getpass("API secret (hidden): ")
	services = detect_services()

	config = build_config(ingest_url, api_key, api_secret, bench_path, site_name, services)
	print_summary(user, bench_path, site_name, ingest_url, api_key, api_secret, services)

	if args.dry_run:
		print("\n(dry run — nothing written)")
		return 0

	if not args.force and not prompt_yes_no("\nProceed?", default=True):
		print("Aborted.")
		return 1

	if existing_install_detected():
		if not args.force and not prompt_yes_no("Existing installation detected. Overwrite?", default=False):
			print("Aborted — existing installation left untouched.")
			return 1
		stop_timer_if_active()

	try:
		run_sudo(["mkdir", "-p", str(INSTALL_DIR), str(CONFIG_DIR)])
		write_agent_script()
		write_config(config, user)
		write_systemd_units(user)
		run_sudo(["systemctl", "daemon-reload"])
	except subprocess.CalledProcessError as e:
		print(f"\nInstall failed: {e}", file=sys.stderr)
		return 1

	if not test_invocation(user):
		print(
			f"\nTest push failed — the timer was NOT enabled. Fix the issue above, then re-run:\n"
			f"  sudo -u {user} /usr/bin/python3 {SCRIPT_DEST} --config {CONFIG_PATH}"
		)
		return 1

	try:
		run_sudo(["systemctl", "enable", "--now", "host-health-agent.timer"])
	except subprocess.CalledProcessError as e:
		print(f"\nTest push succeeded, but enabling the timer failed: {e}", file=sys.stderr)
		return 1

	print(
		"\nDone. The agent will now push every ~25s.\n"
		"Watch it:  sudo journalctl -u host-health-agent.service -f\n"
		"Check the Host Health tab on the dashboard for this host."
	)

	install_resource_agent(user, api_key, api_secret, domain, use_https)
	return 0


if __name__ == "__main__":
	sys.exit(main())
