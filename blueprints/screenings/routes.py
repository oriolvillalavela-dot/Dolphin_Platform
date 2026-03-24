from flask import render_template

from . import screenings_bp


@screenings_bp.route("/")
def dashboard():
    return render_template("screenings/index.html")


@screenings_bp.route("/new")
def new_screening():
    return render_template("screenings/new.html")


@screenings_bp.route("/<string:eln_id>")
def detail(eln_id: str):
    return render_template("screenings/detail.html", eln_id=eln_id)

