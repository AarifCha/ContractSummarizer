from fastapi import APIRouter

from app.api.pdfs import routes_crud, routes_file, routes_qa, routes_section, routes_status
from app.api.pdfs.routes_crud import list_pdfs, upload_pdf

router = APIRouter(prefix="/pdfs", tags=["pdfs"])

# Mount at "" so GET/POST /api/pdfs match without a trailing slash (avoids 307 redirect).
router.add_api_route("", list_pdfs, methods=["GET"])
router.add_api_route("", upload_pdf, methods=["POST"])

# Register literal paths before parameterized `/{pdf_id}` routes where relevant.
router.include_router(routes_section.router)
router.include_router(routes_crud.router)
router.include_router(routes_status.router)
router.include_router(routes_qa.router)
router.include_router(routes_file.router)
