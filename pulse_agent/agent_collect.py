# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Runs inside a bench's own Frappe context — invoked by the host-health
agent script (see ``agent/host_health_agent.py`` in this same app's repo)
via ``bench --site <site> execute pulse_agent.agent_collect.collect_bench_health``,
not imported by the agent script directly. The agent script is plain-stdlib
Python that has to run standalone under systemd with no guarantee it's even
invoked from inside this app's own env; shelling out to ``bench execute``
is what gets it a fully configured Redis connection and RQ classes for
free, the same way ``frappe.utils.doctor`` already introspects workers and
queues, instead of the agent hand-parsing RQ's Redis key scheme itself.

This lives in its own tiny app (``pulse_agent``), separate from the main
``proxmox_monitor`` app that runs the central Pulse site, specifically so
it can ``bench get-app`` cleanly onto every guest in the fleet regardless
of which Frappe/Python version that guest's own bench runs — see this
app's own ``pyproject.toml`` for why its ``requires-python`` is
deliberately permissive where ``proxmox_monitor``'s isn't. Only ``frappe``
and ``rq`` (both guaranteed present in any bench) are used below — no
dependency on anything ``proxmox_monitor``-specific, since this needs to
work even against a Monitored Host record that lives on a completely
different alfaEdge Pulse deployment.

``bench execute`` prints whatever this function returns as
``json.dumps(ret, ...)`` on stdout when the value is truthy (see
``frappe.commands.utils.execute``) — the agent script just parses that
stdout as JSON. This is also why the returned dict always carries at least
one key even when everything is zero: an empty dict is falsy and
``bench execute`` would print nothing at all.
"""

from __future__ import annotations

import os

import frappe
from frappe.utils.background_jobs import get_queue, get_queue_list, get_redis_conn

MAX_FAILED_JOBS_PER_QUEUE = 50
MAX_EXC_INFO_CHARS = 4000


def collect_bench_health() -> dict:
	from rq import Worker

	conn = get_redis_conn()
	workers = Worker.all(connection=conn)
	live_pids = [w.pid for w in workers if w.pid and _pid_alive(w.pid)]

	registered_workers = len(workers)
	live_worker_count = len(live_pids)
	orphan_worker_count = max(registered_workers - live_worker_count, 0)

	queue_depths = {}
	failed_jobs = []
	for queue_name in get_queue_list():
		queue = get_queue(queue_name)
		queue_depths[queue_name] = queue.count
		registry = queue.failed_job_registry
		for job_id in registry.get_job_ids()[:MAX_FAILED_JOBS_PER_QUEUE]:
			job = queue.fetch_job(job_id)
			if not job:
				continue
			failed_jobs.append(
				{
					"rq_job_id": job_id,
					"queue_name": queue_name,
					"failed_at": str(job.ended_at) if job.ended_at else None,
					"exc_info": (job.exc_info or "")[:MAX_EXC_INFO_CHARS],
					"job_name": _job_name(job),
				}
			)

	scheduler_last_run = frappe.db.sql(
		"select max(last_execution) from `tabScheduled Job Type` where stopped = 0"
	)
	scheduler_last_run = scheduler_last_run[0][0] if scheduler_last_run and scheduler_last_run[0][0] else None

	return {
		"registered_workers": registered_workers,
		"live_worker_count": live_worker_count,
		"orphan_worker_count": orphan_worker_count,
		"live_worker_pids": live_pids,
		"queue_depths": queue_depths,
		"failed_job_count": len(failed_jobs),
		"failed_jobs": failed_jobs,
		"scheduler_last_run": str(scheduler_last_run) if scheduler_last_run else None,
	}


def _job_name(job) -> str | None:
	"""The enqueued method's dotted path, used server-side to group
	failures by root cause (same method + same exception type).

	NOT ``job.func_name`` — every job Frappe enqueues runs through
	``frappe.utils.background_jobs.execute_job`` as the actual RQ target,
	so ``func_name`` is the same constant string
	("frappe.utils.background_jobs.execute_job") for every single job,
	regardless of what it actually calls; confirmed live against a real
	failed job. The real identity is one level down, in the kwargs Frappe's
	own ``enqueue()`` passes to that dispatcher: ``job_name`` (its own "more
	readable name", already falling back to the method's dotted path if the
	caller didn't set one — see ``frappe.utils.background_jobs.enqueue``)
	or, failing that, ``method`` itself.

	Guarded the same way ``exc_info`` is already tolerated as possibly
	unavailable — ``job.kwargs`` deserializes pickled data on first access,
	and one unresolvable job shouldn't drop the whole failed-jobs list."""
	try:
		kwargs = job.kwargs or {}
	except Exception:
		return None
	name = kwargs.get("job_name") or kwargs.get("method")
	return name if isinstance(name, str) else None


def _pid_alive(pid: int) -> bool:
	"""A registered RQ worker whose OS process is gone is exactly what
	``orphan_worker_count`` exists to catch — a stale Redis key left behind
	by a worker that crashed instead of deregistering cleanly."""
	try:
		os.kill(pid, 0)
	except ProcessLookupError:
		return False
	except PermissionError:
		# Process exists, just owned by someone else — still alive.
		return True
	return True
