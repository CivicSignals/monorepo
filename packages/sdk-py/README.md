# civicsignals-sdk (Python)

Python client for the CivicSignals REST API. Managed by `uv`.

```python
from civicsignals_sdk import CivicSignalsClient

client = CivicSignalsClient(
    base_url="http://localhost:8000/api/v1",
    token="cs_live_…",
    workspace_id="ws_…",
)
signals = client.request("GET", "/signals?limit=25")
```
