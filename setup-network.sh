#!/bin/bash
# Allow the local IPv4 subnet to reach Cast, without exposing it globally.
set -euo pipefail
command -v ufw >/dev/null || { echo 'UFW is not installed. Allow TCP 49786 from the receiver in your firewall.'; exit 1; }
read -r cast_interface cast_subnet < <(python - <<'PY'
import ipaddress,json,subprocess
routes=json.loads(subprocess.check_output(['ip','-j','route','show','default']))
if not routes: raise SystemExit('No default network connection')
interface=routes[0]['dev']
addresses=json.loads(subprocess.check_output(['ip','-j','-4','address','show','dev',interface]))
for device in addresses:
 for addr in device['addr_info']:
  if addr['scope'] == 'global':
   subnet=ipaddress.ip_network(str(addr['local'])+'/'+str(addr['prefixlen']),strict=False)
   if not subnet.is_private: raise SystemExit('Expected a private local network')
   print(interface,subnet);raise SystemExit(0)
raise SystemExit('No local IPv4 network found')
PY
)
[[ -n ${cast_interface:-} && -n ${cast_subnet:-} ]] || { echo 'Could not determine the local network.' >&2; exit 1; }
args=(ufw allow in on "$cast_interface" from "$cast_subnet" to any port 49786 proto tcp comment 'Omarchy Cast video')
echo "Allowing TCP 49786 from $cast_subnet on $cast_interface."
if (( EUID == 0 )); then "${args[@]}"
elif [[ -t 0 ]]; then sudo "${args[@]}"
else pkexec "${args[@]}"
fi
