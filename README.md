### Pulse Agent

The push agent for alfaEdge Pulse's **Host Health** monitoring — installed on every guest you want OS-service and Frappe-bench health monitored on, separately from the `alfaedge_pulse` app that runs the central alfaEdge Pulse dashboard itself.

This is deliberately a tiny, dependency-free app. The guest fleet runs a mix of Frappe/Python versions; `alfaedge_pulse` pins to whatever Python the central Pulse site happens to run, so installing the *whole* dashboard app onto every monitored guest just to run one collector function was never the right shape. `pulse_agent` has no doctypes and no dependencies beyond `frappe`/`rq` (both present in any bench already), so it installs cleanly regardless of the target bench's version.

### What's in here

- `pulse_agent/agent_collect.py` — runs inside the bench's own Frappe context via `bench execute`, reading RQ worker/queue/failed-job/scheduler data. This is the only part that needs to be a Frappe app.
- `agent/host_health_agent.py` — the actual push agent. Plain standard-library Python, run standalone under systemd — not part of the installed app, doesn't import `frappe` directly.
- `agent/install.py` — interactive installer that sets up everything below (steps 3–6) for you, and optionally the Resource agent right after. See "Installing on a guest".
- `agent/host-health-agent.service` / `agent/host-health-agent.timer` — systemd units (one-shot service triggered every ~25s by the timer).
- `agent/config.example.json` — example agent config, for the manual install path.
- `agent/resource_agent.py` — optional add-on push agent for Resource & Capacity Monitoring (load average, swap, per-mount disk/inode usage). Zero Frappe/bench dependency — see "Resource & Capacity Monitoring" below.
- `agent/resource-agent.service` / `agent/resource-agent.timer` — systemd units for the Resource agent (one-shot, every ~3 minutes).
- `agent/config.example.resource.json` — example Resource agent config.

### Installing on a guest

1. On the central Pulse site, create a **Monitored Host** record for this guest (Link it to its **Proxmox Guest**), then click **Generate / Regenerate Agent Key** and note the `api_key`/`api_secret` — shown once.
2. On the guest, install this app into its own bench:
   ```bash
   cd $PATH_TO_THE_GUEST_BENCH
   bench get-app $URL_OF_THIS_REPO
   bench --site $SITE_NAME install-app pulse_agent
   ```
   (`install-app` is required even though this app has no doctypes — `bench execute` checks the site's installed-apps list.)
3. Run the installer — no root needed to start it; it asks for your `sudo` password only when it actually needs to write a privileged file:
   ```bash
   python3 apps/pulse_agent/agent/install.py
   ```
   It prompts for the OS user, bench path/site (auto-discovered), the Pulse dashboard's domain (just the domain — the API path is filled in for you), and the `api_key`/`api_secret` from step 1; auto-detects installed OS services (SSH/nginx/MariaDB/Redis) and lets you edit the list before continuing. It shows a full summary before writing anything, and — importantly — runs one real test push before enabling the systemd timer, so a wrong URL/key/path is caught immediately with a clear error instead of failing silently every ~25s under `journalctl`. Safe to re-run: it detects an existing install and asks before overwriting.

   Right after Host Health is installed and enabled, it asks whether to also install the **Resource & Capacity Monitoring** collector — reusing the same `api_key`/`api_secret`/domain you just entered, so nothing has to be re-typed. Answer no here and re-run `install.py` any time later to add it.
4. Verify:
   ```bash
   sudo journalctl -u host-health-agent.service -f
   ```

### Resource & Capacity Monitoring (optional add-on)

A separate collector — load average, swap usage, per-mount disk/inode usage — on its own ~3-minute cadence, independent of Host Health's 25s heartbeat. Installed as part of the same `install.py` run (see step 3 above), or by re-running `install.py` later and answering yes to the prompt. Has zero Frappe/bench dependency, so it works on any guest regardless of whether it runs a bench at all. Verify with:
```bash
sudo journalctl -u resource-agent.service -f
```

#### Manual installation

If you'd rather do it by hand (or `install.py` doesn't fit your setup — e.g. an airgapped host), here's what it automates:

```bash
sudo mkdir -p /opt/host-health-agent /etc/host-health-agent
sudo cp agent/host_health_agent.py /opt/host-health-agent/
sudo cp agent/config.example.json /etc/host-health-agent/config.json
```

Edit `/etc/host-health-agent/config.json`:
- `ingest_url` — `https://<pulse-domain>/api/method/alfaedge_pulse.host_health.ingest.push_status` (this still points at the *central* Pulse site's `alfaedge_pulse` app — only the collector moved, not the ingest endpoint)
- `api_key` / `api_secret` — from step 1 above
- `bench_path` / `site_name` — this guest's own bench/site (omit both if this guest runs no Frappe bench at all)
- `services` — adjust `unit_name`s to match the guest's actual distro (`ssh.service` vs `sshd.service`, `mariadb.service` vs `mysql.service`, etc.)

Install the systemd units:
```bash
sudo cp agent/host-health-agent.service agent/host-health-agent.timer /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/host-health-agent.service   # replace __PULSE_AGENT_USER__
sudo systemctl daemon-reload
sudo systemctl enable --now host-health-agent.timer
```

Run it once manually first to catch config mistakes immediately instead of discovering them a cycle later in `journalctl`:
```bash
sudo -u <user> /usr/bin/python3 /opt/host-health-agent/host_health_agent.py --config /etc/host-health-agent/config.json
```

The Resource agent follows the identical shape, under its own paths:
```bash
sudo mkdir -p /opt/resource-agent /etc/resource-agent
sudo cp agent/resource_agent.py /opt/resource-agent/
sudo cp agent/config.example.resource.json /etc/resource-agent/config.json
# edit ingest_url (.../alfaedge_pulse.host_health.ingest.push_resource_metrics) and
# api_key/api_secret — can reuse the same Monitored Host's pair as host-health-agent's config.json
sudo cp agent/resource-agent.service agent/resource-agent.timer /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/resource-agent.service   # replace __PULSE_AGENT_USER__
sudo systemctl daemon-reload
sudo systemctl enable --now resource-agent.timer
```

### License

mit
