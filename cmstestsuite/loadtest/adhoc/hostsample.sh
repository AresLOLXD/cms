#!/usr/bin/env bash

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Sample the host every 10 s, for the whole time the runs take: the Unix
# time, the busy logical CPUs over the interval (from /proc/stat; it
# counts the isolate sandboxes, which docker stats does not), the mean
# clock of the CPUs, the AMD "Tctl" temperature if `sensors` shows one,
# and the 1-minute load average. satwindow.py and windows.py read it.
#   nohup adhoc/hostsample.sh >>out/hostsample.log 2>/dev/null &
set -u
CPUS=$(nproc)
read -r _ u n s i w q sq st _ </proc/stat
prev_busy=$((u + n + s + q + sq + st))
prev_total=$((prev_busy + i + w))
while sleep 10; do
  read -r _ u n s i w q sq st _ </proc/stat
  busy=$((u + n + s + q + sq + st))
  total=$((busy + i + w))
  cores=$(awk -v db=$((busy - prev_busy)) -v dt=$((total - prev_total)) \
    -v cpus="$CPUS" 'BEGIN {printf "%.2f", cpus * db / dt}')
  prev_busy=$busy
  prev_total=$total
  mhz=$(awk '{s += $1; n++} END {printf "%.0f", s / n / 1000}' \
    /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq)
  temp=$(sensors 2>/dev/null | awk '/Tctl/ {print $2}')
  echo "$(date +%s) busy_cores=$cores mhz=$mhz tctl=$temp load=$(cut -d' ' -f1 /proc/loadavg)"
done
