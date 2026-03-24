from flask import Blueprint

lc_ms_bp = Blueprint('lc_ms', __name__, static_folder='../../static/lc_ms', url_prefix='/lc-ms')

from . import routes
from . import api
