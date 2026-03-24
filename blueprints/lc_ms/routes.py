from flask import render_template
from . import lc_ms_bp

@lc_ms_bp.route("/")
def home():
    """
    Serves the single-page application frontend for LC-MS Measurements.
    All data operations are handled by the /api/lc-ms/ endpoints.
    """
    return render_template("lc_ms/index.html")


@lc_ms_bp.route("/ipc")
def ipc():
    return render_template("lc_ms/ipc.html")


@lc_ms_bp.route("/purification")
def purif():
    return render_template("lc_ms/purif.html")


@lc_ms_bp.route("/products")
def products():
    return render_template("lc_ms/products.html")


@lc_ms_bp.route("/ipc-measurements")
def ipc_measurements():
    return render_template("lc_ms/ipc_measurements.html")


@lc_ms_bp.route("/purif-measurements")
def purif_measurements():
    return render_template("lc_ms/purif_measurements.html")
