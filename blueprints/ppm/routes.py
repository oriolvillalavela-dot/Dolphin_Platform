from flask import render_template
from . import ppm_bp


@ppm_bp.route("/")
def home():
    """Serves the PPM single-page dashboard."""
    return render_template("ppm/index.html")
