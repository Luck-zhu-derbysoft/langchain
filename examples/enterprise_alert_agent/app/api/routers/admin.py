

import datetime
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config.dynamic_settings import _dynamic_settings


router = APIRouter(prefix="/admin", tags=["admin"])

def require_admin_token(request: Request):
    token = request.headers.get("X-Admin-Token")
    expected_token = os.getenv("ADMIN_TOKEN", "default-admin-token")
    if token != expected_token:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return token

@router.post("/config/{key}")
async def update_config(key: str, value: any,
                        user_id:str="system",
                        _admin_token:str = Depends(require_admin_token)):
    try:
        success = _dynamic_settings.set(key, value, user_id=user_id)

        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to update config for key: {key}")
        return {
            "success": True,
            "key": key,
            "value": value,
            "timestamp":datetime.datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@router.get("/config")
async def get_all_configs():
    return  _dynamic_settings.get_all_overrides()
@router.get("/config/{key}")
async def get_config(key: str):
    value = _dynamic_settings.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Config for key '{key}' not found")
    return {"key": key, "value": value}
@router.post("/config/{key}/reset")
async def reset_config(key: str,user_id:str="system", _admin_token:str = Depends(require_admin_token)):
    try:
        success = _dynamic_settings.reset(key, user_id=user_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Failed to reset config for key: {key}")
        return {
            "success": True,
            "key": key,
            "message": f"Config {key} reset to default",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@router.get("/config/history")
async def get_config_change_history(limit: int = 50):
    history = _dynamic_settings.get_change_history(limit=limit)
    return {"history": history}
