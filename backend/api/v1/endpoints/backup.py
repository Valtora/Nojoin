from fastapi import APIRouter, UploadFile, File, Query, BackgroundTasks, HTTPException, Depends
from fastapi.responses import FileResponse
from backend.core.backup_manager import BackupManager
from backend.api.deps import get_current_active_superuser
import os
from datetime import datetime

router = APIRouter()

@router.get("/export", dependencies=[Depends(get_current_active_superuser)])
async def export_backup(background_tasks: BackgroundTasks):
    try:
        zip_path = await BackupManager.create_backup()
        
        # Clean up file after sending
        background_tasks.add_task(os.remove, zip_path)
        
        return FileResponse(
            path=zip_path,
            filename=f"nojoin_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            media_type="application/zip"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/import", dependencies=[Depends(get_current_active_superuser)])
async def import_backup(
    file: UploadFile = File(...),
    clear_existing: bool = Query(False, description="Clear existing data before restoring"),
    overwrite_existing: bool = Query(False, description="Overwrite existing recordings if they exist")
):
    # Save uploaded file to temp
    temp_path = f"/tmp/{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        await BackupManager.restore_backup(temp_path, clear_existing, overwrite_existing)
        return {"message": "Backup restored successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
