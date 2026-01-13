from fastapi import APIRouter, UploadFile, File, Query, BackgroundTasks, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from backend.core.backup_manager import BackupManager
from backend.utils.path_manager import PathManager
from backend.api.deps import get_current_active_superuser
from backend.celery_app import celery_app
from celery.result import AsyncResult
import os
import uuid
from datetime import datetime
from typing import Dict, Any

router = APIRouter()

@router.post("/export", dependencies=[Depends(get_current_active_superuser)])
async def export_backup(
    include_audio: bool = Query(True, description="Include audio files in backup")
):
    """
    Trigger background backup creation.
    Returns: {"task_id": str}
    """
    try:
        # Trigger Celery task
        # We use send_task to avoid importing the task function directly (which avoids heavy imports here)
        task = celery_app.send_task(
            "backend.worker.tasks.create_backup_task",
            kwargs={"include_audio": include_audio}
        )
        return {"task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export/{task_id}", dependencies=[Depends(get_current_active_superuser)])
async def get_export_status(task_id: str):
    """
    Get status of backup task.
    Returns: {"status": str, "result": dict | None}
    """
    try:
        task_result = AsyncResult(task_id, app=celery_app)
        
        if task_result.state == 'PENDING':
            response = {
                'state': task_result.state,
                'status': 'Pending...'
            }
        elif task_result.state != 'FAILURE':
            response = {
                'state': task_result.state,
                'status': task_result.info.get('status', '') if isinstance(task_result.info, dict) else str(task_result.info),
                'result': task_result.result if task_result.state == 'SUCCESS' else None
            }
        else:
            # failure
            response = {
                'state': task_result.state,
                'status': str(task_result.info),  # Exception string
            }
            
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export/{task_id}/download", dependencies=[Depends(get_current_active_superuser)])
async def download_export(
    task_id: str,
    background_tasks: BackgroundTasks
):
    """
    Download the result of a completed backup task.
    """
    try:
        task_result = AsyncResult(task_id, app=celery_app)
        
        if task_result.state != 'SUCCESS':
            raise HTTPException(status_code=400, detail="Backup not ready or failed")
        
        result = task_result.result
        if not isinstance(result, dict) or "zip_path" not in result:
             raise HTTPException(status_code=500, detail="Invalid task result format")
             
        zip_path = result["zip_path"]
        if not os.path.exists(zip_path):
             raise HTTPException(status_code=404, detail="Backup file not found (triggered expired?)")
        
        # Helper to clean up file after serving
        def cleanup_file():
             if os.path.exists(zip_path):
                 os.remove(zip_path)
                 
        background_tasks.add_task(cleanup_file)
        
        return FileResponse(
            path=zip_path,
            filename=f"nojoin_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            media_type="application/zip"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import", dependencies=[Depends(get_current_active_superuser)])
async def import_backup(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    clear_existing: bool = Query(False, description="Clear existing data before restoring"),
    overwrite_existing: bool = Query(False, description="Overwrite existing recordings if they exist")
):
    """
    Trigger background backup restoration.
    Returns: {"job_id": str, "message": str}
    """
    # Create persistent temporary connection for the file
    path_manager = PathManager()
    restore_temp_dir = path_manager.user_data_directory / "temp_restores"
    restore_temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean up old temp files (older than 1 day)
    path_manager.cleanup_temp_files(restore_temp_dir, max_age_hours=24)

    job_id = str(uuid.uuid4())
    temp_path = restore_temp_dir / f"{job_id}_{file.filename}"
    
    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Initialize job in manager
        BackupManager.restore_jobs[job_id] = {
            "status": "pending",
            "progress": "Queued",
            "error": None
        }

        # Run in background
        background_tasks.add_task(
            BackupManager.restore_backup, 
            job_id, 
            str(temp_path), 
            clear_existing, 
            overwrite_existing
        )
        
        return JSONResponse(
            status_code=202,
            content={"job_id": job_id, "message": "Restore started"}
        )

    except Exception as e:
        # Cleanup on immediate failure
        if temp_path.exists():
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/import/{job_id}", dependencies=[Depends(get_current_active_superuser)])
async def get_import_status(job_id: str):
    """
    Get status of restore job.
    """
    if job_id not in BackupManager.restore_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return BackupManager.restore_jobs[job_id]

