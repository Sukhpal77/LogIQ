import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes_workspace import SettingInput, KeyInput


@pytest.mark.parametrize('path',['/executor/commands','/workspace/recovery','/workspace/recovery/readiness','/admin/recovery'])
def test_retired_routes_are_unavailable(path):
    assert TestClient(app).get(path).status_code == 404


def test_retired_action_endpoint_is_unavailable():
    assert TestClient(app).post('/action',json={'kind':'restart','target':'api'}).status_code == 404


def test_retired_configuration_and_credentials_are_rejected():
    with pytest.raises(ValidationError):
        SettingInput(kind='automation',name='Old policy')
    with pytest.raises(ValidationError):
        KeyInput(application_id=1,scope='executor')
