## 🚀 Production Migration Playbook

### Phase 1: Environment Reproduction

Before moving code, match the underlying infrastructure.

* **Update System:** Always start with `sudo apt update && sudo apt upgrade -y`.
* **Git & Python:** Install core tools: `sudo apt install git python3-pip python3-dev`.
* **Node.js (Version Specific):** * Use **NVM** (Node Version Manager) to handle specific version requirements.
* Example for your setup: `nvm install 15` then `npm install -g npm@7.5.2`.


* **Process Manager:** Install **PM2** globally: `npm install -g pm2`.
* *Critical:* Run `pm2 startup` and follow the on-screen command to ensure apps survive a reboot.



### Phase 2: Secure Repository Access

Moving from password-based HTTPS to SSH for production stability.

* **SSH Key Generation:** `ssh-keygen -t ed25519 -C "your_email@example.com"`.
* **GitHub Integration:** Copy the public key (`cat ~/.ssh/id_ed25519.pub`) to GitHub Settings.
* **Authenticating:** Run `ssh -T git@github.com` and type **"yes"** to establish the initial trust.
* **Deployment:** Clone the repo and switch to the correct branch: `git clone <ssh-url> && git checkout <branch>`.

### Phase 3: Config & Secrets Migration

Since `.env` files and logs aren't in Git, they must be moved manually.

* **Transfer Secrets:** Copy `config.production.json` and similiar files. 
* **Python Virtual Env:** Create a clean environment to avoid library conflicts:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

```



### Phase 4: Execution & Verification

* **Start the App:** Launch via PM2 (e.g., `pm2 start app.js --name "my-app" --interpreter venv/bin/python`).
* **Local Health Check:** Use `curl http://localhost:8081` to verify the process is responding internally.
* **Log Monitoring:** Use `pm2 logs` or `pm2 monit` to watch for runtime errors.

### Phase 5: Wikimedia Cloud Proxy (Final Step)

* **Testing URL:** Temporarily use the services domain for testing the service or app (e.g., `glamwikidashboardservice.wmcloud.org`) in the Horizon dashboard pointing to your new server's internal port.

---