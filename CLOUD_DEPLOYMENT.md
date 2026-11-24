# Cloud deployment preparation (Oracle Cloud or Google Compute Engine)

This is an additional cloud setup. It does not replace the Mac launch scripts or local Compose file. The cloud Compose definition runs the crypto bot, stock scanner, and dashboard as separate containers, all using the existing simulated crypto broker and the existing persistent `data/` folder.

The cloud instance is intended to start with a fresh paper account and fresh cloud-side data. It does not copy or alter your Mac's `data/` folder; the Mac's current balances and history stay where they are unless you later choose to migrate a copy.

## GitHub's role

GitHub stores and versions the project. `.github/workflows/python.yml` runs the offline tests and checks that the Docker image builds for Oracle's ARM architecture after each push or pull request. The `.devcontainer` setup helps edit and test the code in Codespaces. Codespaces stop after inactivity and have a maximum lifetime, so the live processes belong on the VM. Keep the repository private if you prefer, and never commit `.env`, `data/`, or API credentials; the ignore rules exclude these local files.

## What runs

- `crypto`: BTC/ETH simulated paper trading (`python -m paperbot live`).
- `stocks`: scans all configured symbols (`python -m paperbot stocks`). In the current code this is a signal scanner only; it does not simulate stock orders or maintain a stock trade ledger. The dashboard’s stock $100 display is a starting-balance display, not a live portfolio.
- `dashboard`: the existing Streamlit dashboard. Its host port is bound to `127.0.0.1`, so it is not directly exposed to the public internet.

Each service has an independent Docker restart policy and health check. The installer enables Docker and a systemd Compose unit at boot, so the services come back after a VM reboot. A watchdog checks health every 30 seconds and restarts only a service that remains unhealthy. The shared `data/` bind mount keeps SQLite records, crypto state, models, and bot logs across container rebuilds and restarts. Container console logs are capped with Docker's rotating `local` log driver; the app also keeps its existing rotating `data/bot.log`.

## Oracle Cloud Always Free VM

Oracle's Always Free Ampere A1 allowance can provide up to 2 OCPUs and 12 GB of memory, which is a better fit for the three services than the small Google free VM. In the Oracle Console create a VM using an Ubuntu ARM image and `VM.Standard.A1.Flex`, with no more than 2 OCPUs and 12 GB memory total. Keep it within the Always Free storage and networking limits. Free capacity can be unavailable in some home regions. The application image is built from the project's Python 3.12 Dockerfile; the GitHub checks should pass before using this ARM VM as the production host.

Leave Oracle's maintenance recovery setting at its default restore behavior so an instance that was running before host maintenance is returned to running afterward. A deliberate stop or shutdown by you is treated as intentional and will not be undone by Docker; after a manual VM stop, start it again in the Oracle Console. Once the VM is running, the systemd Compose unit starts the bot services automatically.

Oracle references: [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [instance recovery during maintenance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/edit-maintenance-recovery-action.htm).

## Google Cloud size and cost

The free-tier `e2-micro` is not the recommended target for all three services: it has 1 GiB memory and a fractional CPU. Start at 4 GiB (`e2-medium`) if keeping costs down, and move to 8 GiB (`e2-standard-2`) if memory pressure or slow model updates appear. As of Nov 23, 2026, the list-price estimate for 4 GiB compute is about $24.50/month and 8 GiB about $48.90/month at 730 hours, before disk, network, taxes, and other services. An in-use external IPv4 address is about $3.65/month. Google Free Tier can cover one eligible e2-micro and up to 30 GB-months of standard disk in `us-central1`, `us-east1`, or `us-west1`; outbound data has a 1 GB/month allowance. A billing account is required, and overages are billable. Create a budget alert before starting.

Current provider references: [Compute Free Tier](https://docs.cloud.google.com/free/docs/free-cloud-features), [machine sizes and pricing](https://cloud.google.com/products/compute/pricing/general-purpose), [external IP pricing](https://cloud.google.com/vpc/network-pricing).

## Prepare the VM

1. Create an Ubuntu Compute Engine VM in `us-central1` (Iowa). Choose at least 4 GiB memory, a standard persistent boot disk, and no GPU. Leave **Automatic restart** enabled and choose live migration for host maintenance. Keep the GCP firewall closed for ports 8501, 80, and 443. Restrict SSH access to your own IP or use Google Cloud IAP.
2. Install Docker Engine and the Docker Compose plugin using Docker's official Ubuntu instructions, then enable Docker at boot with `sudo systemctl enable --now docker`. Install Tailscale using its official Linux instructions and sign the VM into the same personal tailnet as your phone and computer.
3. Copy a clean project source copy to `/opt/crypto-paper-bot`; do not upload `data/`, `.venv/`, or any old bot process state as part of the source copy. Create `/opt/crypto-paper-bot/data` and set it writable by the container's `bot` user (UID/GID 1000):

   ```bash
   sudo mkdir -p /opt/crypto-paper-bot/data
   sudo chown -R 1000:1000 /opt/crypto-paper-bot/data
   ```

4. Create `/opt/crypto-paper-bot/.env` on the VM from `.env.example`, and enter only the Alpaca market-data key and secret used by the stock scanner. Set permissions so only the VM owner can read it:

   ```bash
   cd /opt/crypto-paper-bot
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

5. Review `config.yaml`, then start the cloud services:

   ```bash
   cd /opt/crypto-paper-bot
   docker compose -f docker-compose.cloud.yml up -d --build
   sudo ./deploy/cloud/install-watchdog.sh
   docker compose -f docker-compose.cloud.yml ps
   ```

6. Publish the dashboard only to your private Tailscale network. Once Tailscale Serve is enabled for your tailnet, run:

   ```bash
   sudo tailscale serve --bg 8501
   sudo tailscale serve status
   ```

   Install Tailscale on the phone and computer and sign into the same tailnet. Open the HTTPS tailnet URL shown by `tailscale serve status`. Do not use Tailscale Funnel or open port 8501 publicly. See [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) and [Personal plan](https://tailscale.com/pricing).

## Check operations

```bash
cd /opt/crypto-paper-bot
docker compose -f docker-compose.cloud.yml ps
docker compose -f docker-compose.cloud.yml logs --tail=100 crypto stocks dashboard
sudo systemctl status paperbot-compose.service paperbot-watchdog.timer
sudo journalctl -u paperbot-watchdog.service --since today
```

The health checks detect a stale crypto state file, stale stock scan status, or an unresponsive dashboard. The watchdog restarts only the affected service. For VM-level alerts, enable the cloud provider's instance uptime, disk-space, and memory alerts and configure email notifications. The local SQLite database and logs survive restarts on the VM's persistent disk; schedule disk snapshots or a separate backup before treating the VM as the sole copy of the data.

## Paper-only and data limitations

The crypto container runs the existing simulated broker and does not contain exchange order credentials. The stock container only reads Alpaca market bars; the application has no stock order-placement flow. Keep the deployment paper-only and do not add brokerage order endpoints or live trading credentials. The two bots can restart independently, but both still share one VM, so a VM or disk outage affects both until Google restores it. The shared SQLite file is retained to match the current dashboard and schema.
