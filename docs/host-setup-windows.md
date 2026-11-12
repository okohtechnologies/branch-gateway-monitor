# Set up Branch Monitor on a Windows 10/11 host

For a dedicated Windows machine that stays on, sits on the company network (or VPN) that can reach the
branch gateways, and is used only by you. The dashboard listens on the host itself only (`127.0.0.1`),
so nothing is exposed to the network. You start fresh: new database, new admin login.

**Time:** about 45 minutes, most of it waiting for downloads. **You need:** an administrator account on
the host, internet access, your Telegram bot token and chat ID (see step 8), and the IP addresses of a
few branch gateways to test with.

Commands are for **PowerShell**. "As Administrator" means: Start menu, type `PowerShell`, right-click
*Windows PowerShell*, *Run as administrator*. Every step says what you should see; if you don't see it,
go to [Troubleshooting](#troubleshooting) before continuing.

---

## Part A. Prepare the machine

### 1. Update Windows and plug in a cable
- Run Windows Update until nothing is pending, then restart.
- Use **wired Ethernet**, not Wi-Fi. Wi-Fi jitter shows up in every branch's latency graph and can cause
  false alerts. (On this project's first PC, three unrelated branches showed identical latency spikes: it
  was the Wi-Fi.)
- Optional but good: ask your network admin for a fixed address (a DHCP reservation) for this machine.

### 2. Install the tools (As Administrator)
```powershell
winget install -e --id Git.Git
winget install -e --id GitHub.cli
winget install -e --id Docker.DockerDesktop
```
Restart the computer when it finishes.

> Docker Desktop is free for personal use and for companies with fewer than 250 employees and under
> $10M annual revenue. Larger companies need a paid Docker subscription.

### 3. Start Docker Desktop and set it up to start itself
1. Open **Docker Desktop**, accept the terms, and choose **Skip** if it asks you to sign in (an account is
   not needed).
2. Wait until the bottom-left says **Engine running**. The first start can take a few minutes while it
   sets up WSL 2. If it asks you to restart, do.
3. **Settings (gear icon) > General**: tick **Start Docker Desktop when you sign in to your computer**
   and **Use the WSL 2 based engine**. Click **Apply**.
4. If Windows Firewall asks about Docker, choose **Private networks** only.

### 4. Stop the machine sleeping (As Administrator)
A monitor that sleeps monitors nothing.
```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change disk-timeout-ac 0
powercfg /change monitor-timeout-ac 15
powercfg /hibernate off
```
`/hibernate off` also turns off Fast Startup, which can leave Docker half-started after a reboot.

Also, in the computer's BIOS/UEFI, set **Restore on AC Power Loss** (the name varies) to **Power On**, so
it comes back by itself after a power cut. Do this at the next restart if you are not sure how to get in.

In **Settings > Windows Update > Advanced options**, set **Active hours** to your working day, so updates
restart the machine overnight. Do not turn updates off.

### 5. Let it recover from a reboot with nobody there
Docker Desktop only starts after somebody signs in, so for unattended recovery the machine must sign in
by itself.

1. Press **Win + R**, type `netplwiz`, press Enter.
2. Untick **Users must enter a user name and password to use this computer**, click **OK**, and enter the
   account's password twice.
   If that tick box is missing: **Settings > Accounts > Sign-in options**, turn **off** *For improved
   security, only allow Windows Hello sign-in for Microsoft accounts*, then try again.
3. **Recommended:** make the machine lock itself right after it signs in. The monitor keeps running while
   locked. (As Administrator:)
   ```powershell
   schtasks /Create /TN "Lock at sign-in" /TR "rundll32.exe user32.dll,LockWorkStation" /SC ONLOGON /RL LIMITED /F
   ```
   Auto sign-in means anyone with physical access can reach the signed-in session, so keep the machine
   somewhere only you can reach.

---

## Part B. Get the code and check the machine

### 6. Download the project
Open a normal PowerShell window (not as administrator):
```powershell
cd $HOME
gh auth login
```
Choose **GitHub.com**, **HTTPS**, say **Yes** to authenticating Git, and **Login with a web browser**. Sign
in as the account that owns the repository, and enter the one-time code it shows. Then:
```powershell
gh repo clone okohtechnologies/branch-gateway-monitor
cd branch-gateway-monitor
```
You should now be in a folder containing `docker-compose.yml`. Check with `dir`.

### 7. Run the pre-flight check
Put two or three of your real branch gateway addresses after `-Gateway`:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\host_check.ps1 -Gateway 192.168.105.254,192.168.100.254
```
It changes nothing. It prints **PASS**, **WARN** or **FAIL** for each item, and what to do about it:
Windows version, virtualisation, WSL 2, Docker, sleep settings, wired or Wi-Fi, internet access to
Telegram / Docker / PyPI / the heartbeat service, each branch gateway, port 8000, and free disk space.

**Every line must be PASS, except** that a Wi-Fi WARN is acceptable if you really have no cable.
A **gateway FAIL** means this machine cannot reach that branch yet: fix the network/VPN first, because
nothing later will work without it. (If a gateway simply ignores ping, you can add it as a TCP branch
later; the other checks still need to pass.)

---

## Part C. Configure

### 8. Gather your Telegram details
You are **reusing your existing bot**. You need two things (do not copy the old `.env` file):

- **Bot token:** Telegram, chat with `@BotFather`, send `/mybots`, pick your bot, **API Token**. Or copy
  the `TELEGRAM_BOT_TOKEN=` value from the old PC's `.env`.
- **Chat ID:** the `TELEGRAM_CHAT_ID=` value in the old PC's `.env`.
- **Optional, a dead-man's-switch URL** from healthchecks.io: the same ping URL as before works.

### 9. Create the `.env` file
```powershell
powershell -ExecutionPolicy Bypass -File scripts\init_env.ps1
```
It asks, in order:

| Prompt | What to enter |
| --- | --- |
| Bot token | Paste it. Nothing is shown as you paste; that is normal. |
| Chat ID | The digits (a group's ID starts with `-`). |
| healthchecks.io ping URL | Paste it, or press Enter to skip and add it later. |
| Time zone | For example `Africa/Lagos` or `Europe/London`, or press Enter for UTC. |
| Fast checking | `y` = every 5 seconds, outage alert in about 20 seconds (recommended). `n` = every 30 seconds, about 90 seconds. |

It **generates** the database password and the session secret itself, only using characters that cannot
break the connection, checks everything you typed, and never prints your token. You should see
`Created ...\.env`. If a file already exists it stops and tells you; it never overwrites silently.

You can change the monitoring numbers later in the dashboard (Settings), with no restart.

---

## Part D. Start it

### 10. Switch off the old monitor first
Only one program may talk to the Telegram bot at a time, and two monitors would send **duplicate
alerts**. On the **old PC**:
```powershell
cd "c:\dev\IP monitor"
docker compose down
```
(`down` stops it and keeps its data; do not add `-v`.) Then, in Docker Desktop's settings on the old
PC, untick **Start Docker Desktop when you sign in**, so it cannot come back after a restart.

### 11. Build and start on the new host
```powershell
docker compose up -d --build
```
The first run downloads and builds for 5 to 10 minutes. It ends with lines like `Container ...-app-1
Healthy`. Then:
```powershell
docker compose ps
```
You should see three services: `db` (healthy), `app` (healthy) and `backup` (running).

### 12. Check it is healthy
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```
You should see `status : ok`, `db : True`, and `probe_loop` with `ok : True`.

### 13. Create your login
```powershell
docker compose exec app python scripts/create_admin.py you@example.com
```
Use your real email. It asks for a password (at least 10 characters; nothing is shown as you type), then
once more to confirm, and prints `Admin you@example.com is ready.`

### 14. Open the dashboard
In a browser **on the host** go to <http://127.0.0.1:8000> and sign in.

---

## Part E. Set it up and prove it works

### 15. Add your branches
**Branches > Add branch**: a name and the gateway IP for each. Each card shows **Pending** for about 20
seconds (the confirmation checks), then **Up**. A branch that stays down although `host_check` could ping
it: see Troubleshooting.

### 16. Check Telegram
**Settings > Telegram**: it should say *Connected as @your_bot*. Press **Send test message** and confirm
it arrives on your phone. In Telegram, send `/status` to the bot: it should list your branches.

### 17. Check the rest of Settings
- **Monitor health:** every line green. The dead-man's switch says *Healthy* if you gave a URL, or
  *Not set up* if you skipped it.
- **Monitoring:** adjust the interval and the confirmation count if you wish. It applies immediately.

### 18. Test a real alert end to end
1. **Branches > Add branch**: name `Alert test`, address `192.0.2.1` (an address that never answers).
2. Within about 25 seconds Telegram sends a **DOWN** alert with **Acknowledge** and **Mute 1h** buttons.
3. Tap **Acknowledge**: the message updates with your name.
4. Delete the test branch (**Branches > Delete**). You'll get no recovery message, because it never recovered.

---

## Part F. Prove it survives

### 19. The reboot test (do not skip)
Restart the machine and **do not touch it**. After 3 to 5 minutes, from another computer or your phone:
Telegram `/status` should answer. On the host, `Invoke-RestMethod http://127.0.0.1:8000/health` should say
`ok`. If not: auto sign-in (step 5) or Docker's start-on-sign-in (step 3) is not set.

### 20. The dead-man's-switch test (if you set a URL)
```powershell
docker compose stop
```
After the grace time you set on healthchecks.io (about 5 minutes) it should email you that the check is
down. Then `docker compose start`, and it should email that it recovered.

### 21. Look after the backups
A compressed database backup is written to the `backups` folder every 24 hours and the last 14 are kept.
They are on the same disk as the database, so **copy that folder somewhere else now and then**: a USB
drive, a network share, or a cloud folder.

---

## Everyday commands
Run these in the project folder (`cd $HOME\branch-gateway-monitor`).

| Task | Command |
| --- | --- |
| See if it is running | `docker compose ps` |
| Watch the log | `docker compose logs -f app` (Ctrl+C to leave) |
| Stop / start | `docker compose stop` / `docker compose start` |
| Update to a newer version | `git pull` then `docker compose up -d --build` |
| Restart after changing `.env` | `docker compose up -d --force-recreate app` |
| Restore a backup | see the README, "Operations" |

Your data lives in a Docker volume (`pgdata`) and survives restarts, rebuilds and updates.
`docker compose down -v` **deletes it**: never add `-v` unless you mean to wipe everything.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Docker Desktop says "Virtualization is disabled" | Enable Intel VT-x / AMD-V in the BIOS/UEFI, then restart. |
| `docker` is not recognised | Open a **new** PowerShell window after installing; if still missing, restart. |
| `docker compose up` fails with `Failed to resolve 'pypi.org'` or similar | The machine lost internet or Docker's DNS went stale. Run `host_check.ps1` to confirm the internet, then in Docker Desktop choose **Troubleshoot > Restart Docker Desktop**, and retry. |
| App container exits with `Name or service not known` | The database password contains characters that break the URL. Use `init_env.ps1` (it only writes safe characters). |
| Every branch shows **Down** but `host_check` could ping them | Check `docker compose logs app`. Make sure the machine is on the right network/VPN, and that Docker Desktop is fully started. |
| Telegram silent, log mentions a conflict | Another copy is still running (the old PC). Do step 10. |
| Dashboard works but Telegram/heartbeat fail | Check `host_check.ps1` internet lines and any company proxy or firewall. |
| After a reboot nothing starts | Confirm auto sign-in (step 5) and Docker's start-on-sign-in (step 3), and that **Restore on AC power loss** is on. |
| "Port 8000 is already in use" | Something else uses it: `Get-NetTCPConnection -LocalPort 8000` shows the process. |
| You forgot the dashboard password | `docker compose exec app python scripts/create_admin.py you@example.com` resets it and signs out every device. |

## When you are done
Keep the old PC's data for a while as a fallback. When you are sure the new host is stable, free the old
PC's disk with `docker compose down -v` in its project folder. That is permanent.
