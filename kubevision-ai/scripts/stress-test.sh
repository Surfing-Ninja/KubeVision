#!/usr/bin/env bash
set -euo pipefail

MODE=${MODE:-http}
NAMESPACE=${NAMESPACE:-default}
TARGET_POD=${TARGET_POD:-frontend}
SERVICE_URL=${SERVICE_URL:-http://frontend.${NAMESPACE}.svc.cluster.local}
BURSTS=${BURSTS:-3}
DURATION_SECONDS=${DURATION_SECONDS:-20}
SLEEP_SECONDS=${SLEEP_SECONDS:-10}
CONCURRENCY=${CONCURRENCY:-20}

if [[ "$MODE" == "http" ]]; then
	echo "Running bursty HTTP load against ${SERVICE_URL}."
	for ((i=1; i<=BURSTS; i++)); do
		echo "Burst ${i}/${BURSTS}: ${DURATION_SECONDS}s at concurrency ${CONCURRENCY}."
		kubectl run "kv-burst-${i}" \
			--rm -i --restart=Never \
			--image=rakyll/hey \
			-- -z "${DURATION_SECONDS}s" -c "${CONCURRENCY}" "${SERVICE_URL}"
		sleep "${SLEEP_SECONDS}"
	done
	exit 0
fi

if [[ "$MODE" == "memory" ]]; then
	echo "Running bursty memory stress inside pod ${TARGET_POD} in namespace ${NAMESPACE}."
	for ((i=1; i<=BURSTS; i++)); do
		echo "Burst ${i}/${BURSTS}: ${DURATION_SECONDS}s memory spike."
		kubectl exec -n "${NAMESPACE}" "${TARGET_POD}" -- sh -c \
			"command -v stress-ng >/dev/null 2>&1 && stress-ng --vm 1 --vm-bytes 80% --timeout ${DURATION_SECONDS}s"
		sleep "${SLEEP_SECONDS}"
	done
	exit 0
fi

echo "Unknown MODE=${MODE}. Use MODE=http or MODE=memory."
exit 1
