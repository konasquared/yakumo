# yakumo UDP proxying service
A simple service for proxying UDP traffic with nftables.

### Quick Start Guide
Prerequisites:
* Preferably a dedicated server. yakumo is super lightweight and works well on almost anything.
A Raspberry Pi would be overkill (/s).
* Root access on said server
* `nftables` installed on said server

1. Clone this repo to /opt/yakumo
```sh
su
cd /opt
git clone https://github.com/konasquared/yakumo
cd yakumo
```

2. Configure your secret access key in `.env`.
Because of the fact that this code is rushed and that I wanted as little
dependencies as possible, you can't have an `=` character in your access key.
Just bear that in mind.

3. Run `setup.sh`
```sh
chmod +x setup.sh
./setup.sh
exit
```

4. Give commands to the proxy through the API on port 3000

```python
import requests

access_key = "your_access_key_you_made_earlier"

# Create a new proxy tunnel
res = requests.get("proxy.example.com:3000/open-proxy")