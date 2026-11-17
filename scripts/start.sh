#!/bin/sh
# One image, two programs. APP chooses which one runs; the hosting platform sets PORT.
#   APP=api (default)  the web API
#   APP=ui             the chat page
case "${APP:-api}" in
  api)
    exec uvicorn sec_copilot.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  ui)
    exec streamlit run src/sec_copilot/ui/app.py --server.address=0.0.0.0 --server.port="${PORT:-8501}" \
      --server.headless=true --browser.gatherUsageStats=false
    ;;
  *)
    echo "APP must be 'api' or 'ui', not '${APP}'" >&2
    exit 2
    ;;
esac