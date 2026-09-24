#!/usr/bin/with-contenv sh
# with-contenv hands over the container environment, which the base image's
# s6 init keeps from its services otherwise - SUPERVISOR_TOKEN included.
cd /opt/matter_health/app || exit 1
exec python3 -m matter_health
