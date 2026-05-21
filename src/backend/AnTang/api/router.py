from fastapi import APIRouter
from AnTang.api.v1.router import api_v1_router

router = APIRouter()

router.include_router(api_v1_router)