### Pulse Agent

The push agent for alfaEdge Pulse's **Host Health** monitoring — installed on every guest you want OS-service and Frappe-bench health monitored on, separately from the `proxmox_monitor` app that runs the central alfaEdge Pulse dashboard itself.

This is deliberately a tiny, dependency-free app. The guest fleet runs a mix of Frappe/Python versions; `proxmox_monitor` pins to whatever Python the central Pulse site happens to run, so installing the *whole* dashboard app onto every monitored guest just to run one collector function was never the right shape. `pulse_agent` has no doctypes and no dependencies beyond `frappe`/`rq` (both present in any bench already), so it installs cleanly regardless of the target bench's version.

### What's in here

- `pulse_agent/agent_collect.py` — runs inside the bench's own Frappe context via `bench execute`, reading RQ worker/queue/failed-job/scheduler data. This is the only part that needs to be a Frappe app.
- `agent/host_health_agent.py` — the actual push agent. Plain standard-library Python, run standalone under systemd — not part of the installed app, doesn't import `frappe` directly.
- `agent/host-health-agent.service` / `agent/host-health-agent.timer` — systemd units (one-shot service triggered every ~25s by the timer).
- `agent/config.example.json` — example agent config.

### Installing on a guest

1. On the central Pulse site, create a **Monitored Host** record for this guest (Link it to its **Proxmox Guest**), then click **Generate / Regenerate Agent Key** and note the `api_key`/`api_secret` — shown once.
2. On the guest, install this app into its own bench:
   ```bash
   cd $PATH_TO_THE_GUEST_BENCH
   bench get-app $URL_OF_THIS_REPO
   bench --site $SITE_NAME install-app pulse_agent
   ```
   (`install-app` is required even though this app has no doctypes — `bench execute` checks the site's installed-apps list.)
3. Copy `agent/host_health_agent.py` and `agent/config.example.json` onto the guest (they don't need to live inside the bench — anywhere the systemd unit can reach them is fine), e.g.:
   ```bash
   sudo mkdir -p /opt/host-health-agent /etc/host-health-agent
   sudo cp agent/host_health_agent.py /opt/host-health-agent/
   sudo cp agent/config.example.json /etc/host-health-agent/config.json
   ```
4. Edit `/etc/host-health-agent/config.json`:
   - `ingest_url` — `https://<pulse-domain>/api/method/proxmox_monitor.host_health.ingest.push_status` (this still points at the *central* Pulse site's `proxmox_monitor` app — only the collector moved, not the ingest endpoint)
   - `api_key` / `api_secret` — from step 1
   - `bench_path` / `site_name` — this guest's own bench/site (omit both if this guest runs no Frappe bench at all)
   - `services` — adjust `unit_name`s to match the guest's actual distro (`ssh.service` vs `sshd.service`, `mariadb.service` vs `mysql.service`, etc.)
5. Install the systemd units:
   ```bash
   sudo cp agent/host-health-agent.service agent/host-health-agent.timer /etc/systemd/system/
   sudo $EDITOR /etc/systemd/system/host-health-agent.service   # set User= and ExecStart= paths
   sudo systemctl daemon-reload
   sudo systemctl enable --now host-health-agent.timer
   ```
6. Verify:
   ```bash
   sudo journalctl -u host-health-agent.service -f
   ```

### License

mit
