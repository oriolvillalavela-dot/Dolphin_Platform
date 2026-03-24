from flask import Blueprint

screenings_bp = Blueprint("screenings", __name__, url_prefix="/screenings")
screenings_api_bp = Blueprint("screenings_api", __name__, url_prefix="/api/screenings")
plate_designs_api_bp = Blueprint("plate_designs_api", __name__, url_prefix="/api/plate-designs")

from . import routes  # noqa: E402, F401
from . import api  # noqa: E402, F401

