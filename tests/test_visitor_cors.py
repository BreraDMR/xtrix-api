"""Exercise both actual application CORS branches in fresh imports, without startup jobs."""
import json
import os
import secrets
import subprocess
import sys

import pytest


@pytest.mark.parametrize("environment", ["production", "development"])
def test_actual_cors_accepts_visitor_header_only_for_allowed_origins(environment):
    script = r'''
import json
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
try:
    result = {}
    for key, origin in [('allowed','https://visitor.example'),('foreign','https://foreign.example')]:
        response = client.options('/no-visitor-route-installed', headers={
            'Origin':origin, 'Access-Control-Request-Method':'GET',
            'Access-Control-Request-Headers':'X-Visitor-Token'})
        result[key] = {'status':response.status_code,
            'origin':response.headers.get('access-control-allow-origin'),
            'headers':response.headers.get('access-control-allow-headers','').lower()}
    print('CORS_RESULT::'+json.dumps(result))
finally:
    client.close()
'''
    environment_vars = {**os.environ, "JWT_SECRET": secrets.token_hex(32), "ENVIRONMENT": environment, "CORS_ORIGINS": "https://visitor.example",
        "FRONTEND_URL": "https://visitor.example", "SERVICE_PAYMENT_PROVIDER": "", "RATE_LIMIT_ENABLED": "false",
        "LLM_PROVIDER": "fake"}
    result = subprocess.run([sys.executable, "-c", script], env=environment_vars, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(next(line.split("::",1)[1] for line in result.stdout.splitlines() if line.startswith("CORS_RESULT::")))
    assert data["allowed"]["status"] == 200
    assert data["allowed"]["origin"] == "https://visitor.example"
    assert "x-visitor-token" in data["allowed"]["headers"]
    assert data["foreign"]["status"] == 400 and data["foreign"]["origin"] is None
