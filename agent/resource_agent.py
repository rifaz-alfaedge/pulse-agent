#!/usr/bin/env python3
# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Push agent for alfaEdge Pulse's Resource & Capacity Monitoring module.

Deliberately plain standard-library Python, same reasoning as
host_health_agent.py in this same directory (runs standalone under its
own systemd timer, no guarantee of a virtualenv). Unlike that agent, this
one has zero Frappe/bench dependency at all — it only ever reads OS-level
state (load average, swap, per-mount disk/inode usage), so its config is
just `ingest_url`/`api_key`/`api_secret`/`timeout_seconds` (see
config.example.resource.json) — no `bench_path`/`site_name`/`services`.

Designed as an add-on to an already-running host_health_agent.py on the
same guest, sharing that Monitored Host's identity (same api_key/secret
pair) rather than getting a separate heartbeat/identity of its own — see
alfaedge_pulse.host_health.ingest.push_resource_metrics' own docstring for
why it never touches Monitored Host.last_seen/is_online.

Each cycle:
  1. `os.getloadavg()` + `os.cpu_count()` for load average.
  2. Parses `/proc/meminfo`'s SwapTotal/SwapFree for swap usage.
  3. Parses `/proc/mounts` for real (non-pseudo) filesystems, then
     `os.statvfs()` per mount for both block and inode usage in one
     syscall.
  4. POSTs everything in one payload to the ingest endpoint using this
     host's own api_key/api_secret — same Monitored Host credential pair
     host_health_agent.py already has configured.

Config file is JSON — see config.example.resource.json in this directory.
Path defaults to /etc/resource-agent/config.json, override with --config.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_CONFIG_PATH = "/etc/resource-agent/config.json"
DEFAULT_TIMEOUT_SECONDS = 10

# Filesystem types that never represent real, meaningful capacity — no
# point reporting a tmpfs or cgroup pseudo-mount's "disk usage".
PSEUDO_FSTYPES = {
	"tmpfs", "proc", "sysfs", "devtmpfs", "devpts", "overlay", "squashfs",
	"cgroup", "cgroup2", "pstore", "bpf", "tracefs", "debugfs", "mqueue",
	"hugetlbfs", "fusectl", "securityfs", "configfs", "autofs", "binfmt_misc",
	"ramfs", "efivarfs", "rpc_pipefs", "nsfs",
}
# Mount points under these prefixes are virtual/API filesystems even when
# their fstype isn't in PSEUDO_FSTYPES (e.g. some overlay/snap mounts) —
# excluded by path rather than relying on fstype alone.
PSEUDO_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/snap")

logging.basicConfig(
	level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("resource-agent")


def load_config(path: str) -> dict:
	with open(path) as f:
		config = json.load(f)
	for required in ("ingest_url", "api_key", "api_secret"):
		if not config.get(required):
			raise ValueError(f"config is missing required field: {required}")
	return config


def get_load() -> dict:
	one, five, fifteen = os.getloadavg()
	return {
		"load_avg_1min": round(one, 2),
		"load_avg_5min": round(five, 2),
		"load_avg_15min": round(fifteen, 2),
		"cpu_core_count": os.cpu_count() or 1,
	}


def get_swap() -> dict:
	"""Parses /proc/meminfo's SwapTotal/SwapFree — both in kB per Linux's
	own convention. total_gb/used_gb are omitted (None) rather than 0 when
	a host has no swap configured at all, matching Proxmox
	Server.swap_usage's existing null-not-zero convention server-side."""
	total_kb = free_kb = None
	try:
		with open("/proc/meminfo") as f:
			for line in f:
				if line.startswith("SwapTotal:"):
					total_kb = int(line.split()[1])
				elif line.startswith("SwapFree:"):
					free_kb = int(line.split()[1])
	except OSError as e:
		log.warning("could not read /proc/meminfo: %s", e)
		return {"total_gb": None, "used_gb": None}

	if not total_kb:
		return {"total_gb": None, "used_gb": None}
	return {
		"total_gb": round(total_kb / 1e6, 3),
		"used_gb": round((total_kb - (free_kb or 0)) / 1e6, 3),
	}


def _is_real_mount(mount_point: str, fstype: str) -> bool:
	if fstype in PSEUDO_FSTYPES:
		return False
	if mount_point == "/":
		return True
	return not mount_point.startswith(PSEUDO_MOUNT_PREFIXES)


def discover_mounts(include: list[str] | None = None, exclude: list[str] | None = None) -> list[dict]:
	"""Parses /proc/mounts, filters to real filesystems, then os.statvfs()
	per mount for block+inode usage in one syscall each. `include`/
	`exclude` (mount point prefixes) are config-driven overrides for edge
	cases the built-in PSEUDO_FSTYPES/PSEUDO_MOUNT_PREFIXES filter gets
	wrong on an unusual host — most installs need neither."""
	rows = []
	seen_points = set()
	try:
		with open("/proc/mounts") as f:
			lines = f.readlines()
	except OSError as e:
		log.warning("could not read /proc/mounts: %s", e)
		return rows

	for line in lines:
		parts = line.split()
		if len(parts) < 3:
			continue
		mount_point, fstype = parts[1], parts[2]
		if mount_point in seen_points:
			continue  # a later bind-mount of the same point would double-count it

		if include:
			if not any(mount_point == p or mount_point.startswith(p.rstrip("/") + "/") for p in include):
				continue
		elif not _is_real_mount(mount_point, fstype):
			continue
		if exclude and any(mount_point == p or mount_point.startswith(p.rstrip("/") + "/") for p in exclude):
			continue

		try:
			st = os.statvfs(mount_point)
		except OSError as e:
			log.warning("statvfs failed for %s: %s", mount_point, e)
			continue

		seen_points.add(mount_point)
		total_bytes = st.f_frsize * st.f_blocks
		used_bytes = st.f_frsize * (st.f_blocks - st.f_bfree)
		row = {
			"mount_point": mount_point,
			"fstype": fstype,
			"used_bytes": used_bytes,
			"total_bytes": total_bytes,
		}
		# Not every filesystem reports inode counts (some network/overlay
		# filesystems report 0 for both) — omit rather than send a
		# misleading 0/0.
		if st.f_files:
			row["inodes_total"] = st.f_files
			row["inodes_used"] = st.f_files - st.f_ffree
		rows.append(row)

	return rows


def build_payload(config: dict) -> dict:
	include = config.get("mount_include")
	exclude = config.get("mount_exclude")
	return {
		"reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
		"load": get_load(),
		"swap": get_swap(),
		"disks": discover_mounts(include, exclude),
	}


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
	parser = argparse.ArgumentParser(description="alfaEdge Pulse Resource & Capacity Monitoring push agent")
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
