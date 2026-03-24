"""
PPM REST API
=============
All upload/job/project/team-member endpoints for the Project Process Management feature.

URL prefix: /api/ppm/
Blueprint: ppm_api_bp (registered in app.py)
"""

from __future__ import annotations

import os
import uuid
import tempfile
import threading
from datetime import datetime

from flask import request, jsonify, current_app

from . import ppm_api_bp
from .normalization import extract_report_date_from_filename, normalize_molecule_id
from database import SessionLocal
from models import ProcessingJob, MoleculeStatus, ProjectTeamMember, MoleculeSmiles


# ── Background processor ───────────────────────────────────────────────────

def _process_pdf_background(app, job_id: str, tmp_path: str, original_filename: str):
    """
    Runs in a daemon thread. Executes the extraction pipeline, persists results,
    then ALWAYS deletes the temp file (zero-file-storage constraint).
    """
    from .extractor import run_pipeline, ValidationError  # local import — avoids circular

    with app.app_context():
        session = SessionLocal()
        try:
            # Mark job as processing
            job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
            if not job:
                return
            job.status = "processing"
            session.commit()

            # Run the pipeline
            try:
                report_date = extract_report_date_from_filename(original_filename)
                result = run_pipeline(
                    pdf_path=tmp_path,
                    job_id=job_id,
                    week_date_override=report_date,
                    source_filename=original_filename,
                )
            except ValidationError as ve:
                job.status = "error"
                job.error_msg = f"Validation failed: {ve}"
                session.commit()
                return
            except Exception as exc:
                job.status = "error"
                job.error_msg = f"Processing error: {exc}"
                session.commit()
                return

            report_date = result.week_date or extract_report_date_from_filename(original_filename)
            if not report_date:
                job.status = "error"
                job.error_msg = (
                    "Could not determine the report date. "
                    "Expected filename format YYYYMMDD_Report_....pdf."
                )
                session.commit()
                return

            # ── STEP 6: Full validation & storage ──────────────────────────
            valid_records: list[dict] = []
            for rec in result.records:
                normalized = {
                    **rec,
                    "molecule_id": normalize_molecule_id(rec.get("molecule_id")),
                    "week_date": rec.get("week_date") or report_date,
                }
                if all(normalized.get(k) for k in ("project_id", "theme_id", "molecule_id", "status", "week_date")):
                    valid_records.append(normalized)

            # Persist MoleculeStatus records as timeline snapshots keyed by
            # project + molecule + report date. Re-uploads update the same point.
            inserted = 0
            updated = 0
            for rec in valid_records:
                existing = (
                    session.query(MoleculeStatus)
                    .filter_by(
                        project_id=rec["project_id"],
                        molecule_id=rec["molecule_id"],
                        week_date=rec["week_date"],
                    )
                    .first()
                )
                if existing:
                    existing.job_id = job_id
                    existing.theme_id = rec["theme_id"]
                    existing.status = rec["status"]
                    existing.page_number = rec.get("page_number")
                    if rec.get("structure_img"):
                        existing.structure_img = rec.get("structure_img")
                    updated += 1
                else:
                    session.add(
                        MoleculeStatus(
                            job_id=job_id,
                            project_id=rec["project_id"],
                            theme_id=rec["theme_id"],
                            molecule_id=rec["molecule_id"],
                            status=rec["status"],
                            week_date=rec["week_date"],
                            page_number=rec.get("page_number"),
                            structure_img=rec.get("structure_img"),
                        )
                    )
                    inserted += 1

            # ── Auto-populate SMILES from MolScribe predictions ─────────────
            # For each record where the extractor returned a SMILES, insert into
            # ppm_molecule_smiles (only if no user-provided entry already exists).
            # User-entered SMILES always take priority over MolScribe predictions.
            for rec in valid_records:
                ml_smiles = rec.get("smiles")
                if not ml_smiles:
                    continue
                existing_smiles = (
                    session.query(MoleculeSmiles)
                    .filter_by(
                        project_id=rec["project_id"],
                        molecule_id=rec["molecule_id"],
                    )
                    .first()
                )
                if not existing_smiles:
                    # Use the PDF-extracted image as the structure preview;
                    # the SMILES came from MolScribe image recognition.
                    session.add(MoleculeSmiles(
                        project_id=rec["project_id"],
                        molecule_id=rec["molecule_id"],
                        smiles=ml_smiles,
                        structure_img=rec.get("structure_img"),
                    ))

            # Update job
            job.week_date = report_date
            job.flagged_for_review = bool(result.flagged_pages)
            job.status = "review" if result.flagged_pages else "done"
            if result.flagged_pages:
                job.error_msg = (
                    f"Pages flagged for manual review: {sorted(result.flagged_pages)}. "
                    f"{inserted} inserted, {updated} updated."
                )
            else:
                job.error_msg = None
            session.commit()

        except Exception as exc:
            session.rollback()
            try:
                job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
                if job:
                    job.status = "error"
                    job.error_msg = f"Unexpected error: {exc}"
                    session.commit()
            except Exception:
                pass
        finally:
            session.close()
            # ── ZERO FILE STORAGE: always delete the temp file ─────────────
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass  # best-effort deletion


# ── Upload endpoint ────────────────────────────────────────────────────────

@ppm_api_bp.route("/upload", methods=["POST"])
def upload_pdf():
    """
    POST /api/ppm/upload
    Accepts multipart PDF upload. Immediately creates a ProcessingJob record
    and spawns a background thread for pipeline execution.
    Returns: { job_id, status }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"error": "No file selected."}), 400

    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted."}), 400

    # Write to a named temp file (not auto-deleted so the background thread can read it)
    try:
        suffix = ".pdf"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        f.save(tmp.name)
        tmp.close()
        tmp_path = tmp.name
    except Exception as exc:
        return jsonify({"error": f"Failed to save temporary file: {exc}"}), 500

    job_id = str(uuid.uuid4())
    uploader = request.form.get("uploader", "").strip() or None

    session = SessionLocal()
    try:
        job = ProcessingJob(
            job_id=job_id,
            filename=f.filename,
            upload_ts=datetime.utcnow(),
            uploader=uploader,
            status="pending",
        )
        session.add(job)
        session.commit()
    except Exception as exc:
        session.rollback()
        session.close()
        # Clean up temp file if DB registration failed
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return jsonify({"error": f"Failed to register job: {exc}"}), 500
    finally:
        session.close()

    # Spawn background processing thread
    app = current_app._get_current_object()  # type: ignore[attr-defined]
    thread = threading.Thread(
        target=_process_pdf_background,
        args=(app, job_id, tmp_path, f.filename),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "pending"}), 202


# ── Job polling ────────────────────────────────────────────────────────────

@ppm_api_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    """
    GET /api/ppm/jobs/<job_id>
    Poll job status.
    Returns: { job_id, status, error_msg, week_date, flagged_for_review, record_count }
    """
    session = SessionLocal()
    try:
        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            return jsonify({"error": "Job not found."}), 404

        record_count = session.query(MoleculeStatus).filter_by(job_id=job_id).count()

        return jsonify({
            "job_id": job.job_id,
            "filename": job.filename,
            "status": job.status,
            "error_msg": job.error_msg,
            "week_date": job.week_date,
            "flagged_for_review": job.flagged_for_review,
            "record_count": record_count,
            "upload_ts": job.upload_ts.isoformat() if job.upload_ts else None,
        })
    finally:
        session.close()


@ppm_api_bp.route("/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id: str):
    """DELETE /api/ppm/jobs/<job_id> — remove job and all its records."""
    session = SessionLocal()
    try:
        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            return jsonify({"error": "Job not found."}), 404
        session.delete(job)
        session.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        session.close()


# ── Jobs list ──────────────────────────────────────────────────────────────

@ppm_api_bp.route("/jobs", methods=["GET"])
def list_jobs():
    """
    GET /api/ppm/jobs
    Returns list of recent processing jobs (newest first, limit 50).
    """
    session = SessionLocal()
    try:
        jobs = (
            session.query(ProcessingJob)
            .order_by(ProcessingJob.upload_ts.desc())
            .limit(50)
            .all()
        )
        items = []
        for job in jobs:
            rc = session.query(MoleculeStatus).filter_by(job_id=job.job_id).count()
            items.append({
                "job_id": job.job_id,
                "filename": job.filename,
                "status": job.status,
                "week_date": job.week_date,
                "flagged_for_review": job.flagged_for_review,
                "record_count": rc,
                "upload_ts": job.upload_ts.isoformat() if job.upload_ts else None,
            })
        return jsonify({"items": items})
    finally:
        session.close()


# ── Projects ───────────────────────────────────────────────────────────────

@ppm_api_bp.route("/projects", methods=["GET"])
def list_projects():
    """
    GET /api/ppm/projects
    Returns one row per project with molecule counts, latest week date, and
    associated theme IDs.
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(
                MoleculeStatus.project_id,
                MoleculeStatus.theme_id,
                MoleculeStatus.molecule_id,
                MoleculeStatus.week_date,
            )
            .order_by(MoleculeStatus.project_id.asc())
            .all()
        )

        projects: dict[str, dict] = {}
        for row in rows:
            item = projects.setdefault(
                row.project_id,
                {
                    "project_id": row.project_id,
                    "theme_ids": set(),
                    "molecule_ids": set(),
                    "latest_week": "",
                },
            )
            if row.theme_id:
                item["theme_ids"].add(row.theme_id)
            if row.molecule_id:
                item["molecule_ids"].add(normalize_molecule_id(row.molecule_id))
            if row.week_date and row.week_date > item["latest_week"]:
                item["latest_week"] = row.week_date

        items = []
        for project_id in sorted(projects):
            item = projects[project_id]
            theme_ids = sorted(item["theme_ids"])
            items.append({
                "project_id": project_id,
                "theme_id": theme_ids[0] if len(theme_ids) == 1 else None,
                "theme_ids": theme_ids,
                "molecule_count": len(item["molecule_ids"]),
                "latest_week": item["latest_week"] or None,
            })

        return jsonify({"items": items})
    finally:
        session.close()


@ppm_api_bp.route("/project/<project_id>", methods=["GET"])
def get_project_dashboard(project_id: str):
    """
    GET /api/ppm/project/<project_id>
    Full dashboard payload: theme, team members, all molecule records, Gantt series.
    """
    session = SessionLocal()
    try:
        # All records for this project
        records = (
            session.query(MoleculeStatus)
            .filter_by(project_id=project_id)
            .order_by(MoleculeStatus.week_date.asc(), MoleculeStatus.molecule_id.asc())
            .all()
        )

        if not records:
            return jsonify({"error": f"No data found for project {project_id}."}), 404

        # Team members
        members = (
            session.query(ProjectTeamMember)
            .filter_by(project_id=project_id)
            .order_by(ProjectTeamMember.member_name.asc())
            .all()
        )

        theme_ids = sorted({r.theme_id for r in records if r.theme_id})
        week_dates = sorted({r.week_date for r in records if r.week_date})

        # User-provided SMILES structure images (take priority over PDF-extracted)
        smiles_rows = (
            session.query(MoleculeSmiles)
            .filter_by(project_id=project_id)
            .all()
        )
        smiles_struct_map: dict[str, dict[str, str | None]] = {}
        for row in smiles_rows:
            mol_id = normalize_molecule_id(row.molecule_id)
            if not mol_id:
                continue
            current = smiles_struct_map.get(mol_id)
            candidate = {"smiles": row.smiles, "structure_img": row.structure_img}
            if current is None or (candidate["structure_img"] and not current.get("structure_img")):
                smiles_struct_map[mol_id] = candidate

        merged_records: dict[tuple[str, str], dict] = {}
        for r in records:
            mol_id = normalize_molecule_id(r.molecule_id)
            if not mol_id:
                continue
            smiles_info = smiles_struct_map.get(mol_id, {})
            payload = {
                "id": r.id,
                "molecule_id": mol_id,
                "status": r.status,
                "week_date": r.week_date,
                "theme_id": r.theme_id,
                "page_number": r.page_number,
                "structure_img": smiles_info.get("structure_img") or r.structure_img,
                "smiles": smiles_info.get("smiles") or "",
            }
            key = (mol_id, r.week_date or "")
            existing = merged_records.get(key)
            if existing is None or (payload["structure_img"] and not existing.get("structure_img")):
                merged_records[key] = payload

        records_payload = sorted(
            merged_records.values(),
            key=lambda rec: ((rec.get("week_date") or ""), rec["molecule_id"]),
        )
        molecule_ids = sorted({rec["molecule_id"] for rec in records_payload})
        records_by_molecule: dict[str, list[dict]] = {}
        for rec in records_payload:
            records_by_molecule.setdefault(rec["molecule_id"], []).append(rec)

        parsed_week_dates = []
        for value in week_dates:
            try:
                parsed_week_dates.append(datetime.fromisoformat(value).date())
            except ValueError:
                continue
        project_latest_date = max(parsed_week_dates).isoformat() if parsed_week_dates else (week_dates[-1] if week_dates else "")

        gantt_series = []
        for mol in molecule_ids:
            snapshots = sorted(
                records_by_molecule.get(mol, []),
                key=lambda rec: (rec.get("week_date") or ""),
            )
            weeks = {rec["week_date"] or "": rec["status"] for rec in snapshots}
            segments: list[dict] = []

            if snapshots:
                run_start = 0
                for idx in range(1, len(snapshots)):
                    prev = snapshots[idx - 1]
                    current = snapshots[idx]
                    if current["status"] != prev["status"]:
                        start_rec = snapshots[run_start]
                        segments.append({
                            "status": start_rec["status"],
                            "start_date": start_rec["week_date"] or "",
                            "end_date": current["week_date"] or start_rec["week_date"] or "",
                        })
                        run_start = idx

                last_start = snapshots[run_start]
                segments.append({
                    "status": last_start["status"],
                    "start_date": last_start["week_date"] or "",
                    "end_date": project_latest_date or last_start["week_date"] or "",
                })

            gantt_series.append({
                "molecule_id": mol,
                "weeks": weeks,
                "segments": segments,
            })

        return jsonify({
            "project_id": project_id,
            "theme_ids": theme_ids,
            "team_members": [m.member_name for m in members],
            "molecules": molecule_ids,
            "week_dates": week_dates,
            "records": records_payload,
            "gantt_series": gantt_series,
            "smiles_map": {k: v["smiles"] for k, v in smiles_struct_map.items() if v.get("smiles")},
        })
    finally:
        session.close()


# ── Team Members ───────────────────────────────────────────────────────────

@ppm_api_bp.route("/project/<project_id>/members", methods=["GET"])
def get_team_members(project_id: str):
    session = SessionLocal()
    try:
        members = (
            session.query(ProjectTeamMember)
            .filter_by(project_id=project_id)
            .order_by(ProjectTeamMember.member_name.asc())
            .all()
        )
        return jsonify({"members": [m.member_name for m in members]})
    finally:
        session.close()


@ppm_api_bp.route("/project/<project_id>/members", methods=["POST"])
def add_team_member(project_id: str):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("member_name") or "").strip()
    if not name:
        return jsonify({"error": "member_name is required."}), 400

    session = SessionLocal()
    try:
        existing = (
            session.query(ProjectTeamMember)
            .filter_by(project_id=project_id, member_name=name)
            .first()
        )
        if existing:
            return jsonify({"ok": True, "member_name": name})  # idempotent

        session.add(ProjectTeamMember(project_id=project_id, member_name=name))
        session.commit()
        return jsonify({"ok": True, "member_name": name}), 201
    except Exception as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        session.close()


@ppm_api_bp.route("/project/<project_id>/members/<member_name>", methods=["DELETE"])
def delete_team_member(project_id: str, member_name: str):
    session = SessionLocal()
    try:
        m = (
            session.query(ProjectTeamMember)
            .filter_by(project_id=project_id, member_name=member_name)
            .first()
        )
        if not m:
            return jsonify({"error": "Member not found."}), 404
        session.delete(m)
        session.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        session.close()


# ── SMILES & Structure Generation ──────────────────────────────────────────

def _smiles_to_png_b64(smiles: str):
    """Generate a 300×300 PNG from a SMILES string using RDKit. Returns base64 str or None."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw
        import io as _io, base64 as _b64
        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None:
            return None
        img = Draw.MolToImage(mol, size=(300, 300))
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return _b64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None


@ppm_api_bp.route("/project/<project_id>/structure", methods=["GET"])
def list_smiles(project_id: str):
    """GET /api/ppm/project/<id>/structure — return all stored SMILES for a project."""
    session = SessionLocal()
    try:
        rows = session.query(MoleculeSmiles).filter_by(project_id=project_id).all()
        return jsonify({
            "items": [
                {
                    "molecule_id": r.molecule_id,
                    "smiles": r.smiles,
                    "has_structure": bool(r.structure_img),
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ]
        })
    finally:
        session.close()


@ppm_api_bp.route("/project/<project_id>/structure", methods=["POST"])
def set_smiles(project_id: str):
    """
    POST /api/ppm/project/<id>/structure
    Body: { molecule_id: str, smiles: str }
    Generates a 2D PNG via RDKit and stores both. Upserts on conflict.
    """
    data = request.get_json(force=True, silent=True) or {}
    mol_id = (data.get("molecule_id") or "").strip()
    smiles  = (data.get("smiles")      or "").strip()

    if not mol_id:
        return jsonify({"error": "molecule_id is required."}), 400
    if not smiles:
        return jsonify({"error": "smiles is required."}), 400

    # Generate structure PNG
    structure_img = _smiles_to_png_b64(smiles)
    if structure_img is None:
        return jsonify({"error": "Invalid SMILES string — could not parse with RDKit."}), 422

    session = SessionLocal()
    try:
        row = (
            session.query(MoleculeSmiles)
            .filter_by(project_id=project_id, molecule_id=mol_id)
            .first()
        )
        if row:
            row.smiles = smiles
            row.structure_img = structure_img
        else:
            session.add(MoleculeSmiles(
                project_id=project_id,
                molecule_id=mol_id,
                smiles=smiles,
                structure_img=structure_img,
            ))
        session.commit()
        return jsonify({
            "ok": True,
            "molecule_id": mol_id,
            "structure_img": structure_img,
        }), 201
    except Exception as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        session.close()


@ppm_api_bp.route("/project/<project_id>/structure/<molecule_id>", methods=["DELETE"])
def delete_smiles(project_id: str, molecule_id: str):
    """DELETE /api/ppm/project/<id>/structure/<mol_id> — remove SMILES and structure."""
    session = SessionLocal()
    try:
        row = (
            session.query(MoleculeSmiles)
            .filter_by(project_id=project_id, molecule_id=molecule_id)
            .first()
        )
        if not row:
            return jsonify({"error": "No SMILES found for this molecule."}), 404
        session.delete(row)
        session.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        session.close()
