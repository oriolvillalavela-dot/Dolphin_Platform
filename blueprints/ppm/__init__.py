from flask import Blueprint

ppm_bp = Blueprint('ppm', __name__, url_prefix='/ppm')
ppm_api_bp = Blueprint('ppm_api', __name__, url_prefix='/api/ppm')

from . import routes  # noqa: E402, F401
from . import api     # noqa: E402, F401
