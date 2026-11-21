# Reaching data.gov.in from a server outside India

api.data.gov.in refuses connections from many non-Indian IPs (an AWS Sydney server gets
"Couldn't connect to server"). The app can stay where it is: run a tiny forward proxy on a
small server in India and set `DATA_GOV_PROXY` so that only the price download goes through it.

The proxy only tunnels HTTPS, so the API key stays encrypted between the app and data.gov.in.

## 1. Mumbai instance

In Lightsail: create an Ubuntu 24.04 instance in **Mumbai (ap-south-1)**, the smallest plan
**with a public IPv4 address** (IPv6-only plans can't reach data.gov.in). Attach a static IP.
In its IPv4 firewall, add **TCP 8888** and restrict the source to the app server's static IP.
Also restrict SSH (22) to the app server's IP if you only reach it through that server.

Connect with the browser SSH button in the Lightsail console, or through the app server:
`ssh -J ubuntu@APP_SERVER_IP -i mumbai-key.pem ubuntu@MUMBAI_IP` (download the Mumbai
default key from Lightsail > Account > SSH keys; it differs from the Sydney one).

## 2. Proxy

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070?api-key=YOUR_KEY&format=json&limit=1"   # expect 200
sudo apt update && sudo apt install -y tinyproxy
echo '^api\.data\.gov\.in$' | sudo tee /etc/tinyproxy/filter
sudo tee /etc/tinyproxy/tinyproxy.conf >/dev/null <<'CONF'
User tinyproxy
Group tinyproxy
Port 8888
Timeout 120
LogFile "/var/log/tinyproxy/tinyproxy.log"
LogLevel Warning
MaxClients 20
# Only the app server may use the proxy (its public static IP).
Allow APP_SERVER_IP
# Only HTTPS, and only to data.gov.in's API.
ConnectPort 443
Filter "/etc/tinyproxy/filter"
FilterType ere
FilterDefaultDeny Yes
CONF
sudo sed -i 's/APP_SERVER_IP/13.238.67.217/' /etc/tinyproxy/tinyproxy.conf   # your app server's IP
sudo systemctl restart tinyproxy && sudo systemctl enable tinyproxy
```

## 3. App server

```bash
curl -sS -x http://MUMBAI_IP:8888 -o /dev/null -w "HTTP %{http_code}\n" "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070?api-key=YOUR_KEY&format=json&limit=1"   # expect 200
```

Then set `DATA_GOV_PROXY=http://MUMBAI_IP:8888` in `.env` and run `flask --app wsgi ingest`.
