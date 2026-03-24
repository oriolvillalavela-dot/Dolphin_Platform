import uuid
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, Float, Text, Date, DateTime, func, UniqueConstraint, JSON, ForeignKey, Boolean

Base = declarative_base()

class Chemical(Base):
    __tablename__ = "chemicals"
    id = Column(Integer, primary_key=True)
    chem_id = Column(String(32), unique=True, index=True)
    common_name_abb = Column(String(255), nullable=False)
    cas = Column(String(64))
    ro_srn = Column(String(64))
    chemform = Column(String(128))
    mw = Column(Float)
    mim = Column(String(128))
    density = Column(Float)
    aggregate_state = Column(String(32))
    stock_solution_c = Column(String(64))
    purity = Column(Float)
    smiles = Column(Text)
    inchi = Column(Text)
    inchi_key = Column(String(255))
    functional_groups = Column(JSON)
    structure_svg = Column(Text)

    def to_dict(self):
        return {
            "chem_id": self.chem_id,
            "common_name_abb": self.common_name_abb,
            "cas": self.cas, "ro_srn": self.ro_srn, "chemform": self.chemform,
            "mw": self.mw, "mim": self.mim, "density": self.density,
            "aggregate_state": self.aggregate_state, "stock_solution_c": self.stock_solution_c,
            "purity": self.purity, "smiles": self.smiles, "inchi": self.inchi, "inchi_key": self.inchi_key,
            "functional_groups": self.functional_groups
        }

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, index=True, nullable=False)

class Bottle(Base):
    __tablename__ = "bottle_db"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
    bottle_id = Column(String(255), unique=True, index=True)  # Chem_1_B1
    chem_id = Column(String(32), index=True)                  # Chem_1
    supplier_id = Column(String(255), nullable=False)         # supplier name
    lot_no = Column(String(255), nullable=False)
    purity = Column(Float, nullable=False)
    size_amount = Column(String(128), nullable=False)
    barcode     = Column(String, nullable=True)

class Batch(Base):
    __tablename__ = "batch_db"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
    batch_id = Column(String(255), unique=True, index=True)   # Chem_1_B1_B1 / _S1 / _H1...
    chem_id = Column(String(32), index=True)
    kind = Column(String(32))               # Bottle / Stock solution / Head
    bottle_no = Column(Integer)             # from Chem_X_B<no>
    kind_index = Column(Integer)            # trailing number after _B/_S/_H
    concentration_moll = Column(Float, nullable=True)
    barcode = Column(String(255), nullable=False)
    location = Column(String(255), nullable=False)
    sublocation = Column(String(255))
    amount = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default="Available")
    expiring_date = Column(Date)

class Plate(Base):
    __tablename__ = "plates"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
    eln_id = Column(String(32), index=True, nullable=False)
    plate_no = Column(Integer, nullable=False)
    plate_type = Column(String(8), nullable=False)  # 24 or 96
    atmosphere = Column(String(128))
    mixing = Column(String(64))           # st_XXX / sh_XXX
    wavelength_nm = Column(Float)
    scale_mol = Column(Float)
    concentration_mol_l = Column(Float)

class PlateWellReagent(Base):
    __tablename__ = "plate_well_reagents"
    id = Column(Integer, primary_key=True)
    plate_id = Column(Integer, index=True, nullable=False)
    well = Column(String(8), index=True, nullable=False)  # e.g., A1
    chem_id = Column(String(32), nullable=False)
    category = Column(String(32), nullable=False)         # starting_material / reagent / solvent / ...
    amount = Column(Float)

class SurfRow(Base):
    __tablename__ = "surf"
    id = Column(Integer, primary_key=True)
    plate_id = Column(Integer, index=True, nullable=False)

    eln_id = Column(String(32), index=True, nullable=False)
    plate_no = Column(Integer, nullable=False)
    well = Column(String(8), index=True, nullable=False)
    mixing = Column(String(64))
    atmosphere = Column(String(128))
    wavelength_nm = Column(Float)
    scale_mol = Column(Float)
    concentration_mol_l = Column(Float)

    startingmat_1_id = Column(String(32))
    startingmat_1_eq = Column(Float)
    startingmat_2_id = Column(String(32))
    startingmat_2_eq = Column(Float)

    reagent_1_id = Column(String(32))
    reagent_1_eq = Column(Float)
    reagent_2_id = Column(String(32))
    reagent_2_eq = Column(Float)
    reagent_3_id = Column(String(32))
    reagent_3_eq = Column(Float)
    reagent_4_id = Column(String(32))
    reagent_4_eq = Column(Float)
    reagent_5_id = Column(String(32))
    reagent_5_eq = Column(Float)

    solvent_1_id = Column(String(32))
    solvent_1_fraction = Column(Float)
    solvent_2_id = Column(String(32))
    solvent_2_id = Column(String(32))
    solvent_2_fraction = Column(Float)

# ------------------- LC-MS Module Models ------------------- #

class Chemist(Base):
    __tablename__ = "chemists"
    username = Column(String, primary_key=True)  # e.g. "villalao"
    user_id = Column(String, nullable=False)     # e.g. "OV"

class ELN(Base):
    __tablename__ = "elns"
    eln_id = Column(String, primary_key=True)
    chemist = Column(String, nullable=False)  # references Chemist.username (not enforced)
    stmat_1_chemform = Column(String, nullable=True)
    stmat_2_chemform = Column(String, nullable=True)
    product_1_chemform = Column(String, nullable=True)
    product_2_chemform = Column(String, nullable=True)
    product_3_chemform = Column(String, nullable=True)
    product_4_chemform = Column(String, nullable=True)

    # Hidden numeric ID used for ordering (seeded from Excel "ID" column; next = max+1 on new)
    order_id = Column(Integer, index=True, nullable=True)

    def chemform_list(self):
        vals = [self.stmat_1_chemform, self.stmat_2_chemform, self.product_1_chemform, self.product_2_chemform, self.product_3_chemform, self.product_4_chemform]
        return [v for v in vals if v and str(v).strip()]

    def product_chemforms(self):
        vals = [self.product_1_chemform, self.product_2_chemform, self.product_3_chemform, self.product_4_chemform]
        return [v for v in vals if v and str(v).strip()]

class IPCMeasurement(Base):
    __tablename__ = "ipc_measurements"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chemist_username = Column(String, nullable=False)
    eln_id = Column(String, nullable=False)
    ipc_no = Column(Integer, nullable=False)
    duration_h = Column(Integer, nullable=False)
    lc_ms_method_min = Column(String, nullable=True)
    lc_ms_instrument = Column(String, nullable=True)
    lc_ms_file_name = Column(String, nullable=True)
    ipc_result = Column(String, nullable=True)
    __table_args__ = (UniqueConstraint('eln_id', 'ipc_no', name='uq_ipc_eln_ipcno'),)

class PurifMeasurement(Base):
    __tablename__ = "purif_measurements"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chemist_username = Column(String, nullable=False)
    eln_id = Column(String, nullable=False)
    purif_no = Column(Integer, nullable=False)
    fraction_no = Column(Integer, nullable=False)          # keeps numeric part
    fraction_label = Column(String, nullable=True)          # NEW: keeps "1-3", "1+2", etc.
    purif_method = Column(String, nullable=True)
    analysis_instrument = Column(String, nullable=True)
    analysis_file_name = Column(String, nullable=True)
    purif_result = Column(String, nullable=True)
    __table_args__ = (UniqueConstraint('eln_id','purif_no','fraction_no', name='uq_purif_eln_purif_frac'),)

class QCResult(Base):
    __tablename__ = "qc_results"
    id = Column(Integer, primary_key=True)
    batch_id = Column(String(255), index=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    
    chem_found = Column(String(16))  # "YES" / "NO"
    found_mass = Column(Float, nullable=True)
    retention_time = Column(Float, nullable=True)
    purity = Column(String(32))      # "pure" / "impure"
    purity_percent = Column(Float, nullable=True) # Optional: store exact %
    
    # Metadata
    filename = Column(String(255))
    analysis_log = Column(Text, nullable=True)

# ------------------- Requests Module Models ------------------- #

class Experiment(Base):
    __tablename__ = "experiments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    eln_id = Column(String, nullable=False, unique=True)
    project_id = Column(String, nullable=True)
    project_name = Column(String, nullable=True)
    theme = Column(String, nullable=True)
    reaction_type = Column(String, nullable=True)
    start_date = Column(String, nullable=True)
    type = Column(String, nullable=True)
    completion_date = Column(String, nullable=True)
    no_reactions = Column(Integer, nullable=True)
    no_reg_compounds = Column(Integer, nullable=True)
    success_rate = Column(Float, nullable=True)
    
    # Crucial for the HTML visualization
    details = relationship("ExperimentDetails", backref="experiment_parent", cascade="all, delete-orphan")

class ExperimentDetails(Base):
    __tablename__ = "experimentdetails"
    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(Integer, ForeignKey('experiments.id'), nullable=False)
    
    scale_mmol = Column(Float, nullable=True)
    conc_moll = Column(Float, nullable=True)

    startingmat_1_id = Column(String, nullable=True)
    startingmat_2_id = Column(String, nullable=True)
    reagent_1_id = Column(String, nullable=True)
    reagent_2_id = Column(String, nullable=True)
    reagent_3_id = Column(String, nullable=True)
    reagent_4_id = Column(String, nullable=True)
    reagent_5_id = Column(String, nullable=True)
    solvent_1_id = Column(String, nullable=True)
    solvent_2_id = Column(String, nullable=True)
    # --- NEW COLUMNS FOR ORIGINAL EXCEL VALUES ---
    startingmat_1_raw = Column(String, nullable=True)
    startingmat_2_raw = Column(String, nullable=True)
    reagent_1_raw = Column(String, nullable=True)
    reagent_2_raw = Column(String, nullable=True)
    reagent_3_raw = Column(String, nullable=True)
    reagent_4_raw = Column(String, nullable=True)
    reagent_5_raw = Column(String, nullable=True)
    solvent_1_raw = Column(String, nullable=True)
    solvent_2_raw = Column(String, nullable=True)
    # --- NEW COLUMNS FOR EQ AND FRACTIONS ---
    startingmat_1_eq = Column(Float, nullable=True)
    startingmat_2_eq = Column(Float, nullable=True)
    reagent_1_eq = Column(Float, nullable=True)
    reagent_2_eq = Column(Float, nullable=True)
    reagent_3_eq = Column(Float, nullable=True)
    reagent_4_eq = Column(Float, nullable=True)
    reagent_5_eq = Column(Float, nullable=True)
    solvent_1_fraction = Column(Float, nullable=True)
    solvent_2_fraction = Column(Float, nullable=True)
    wavelength_nm = Column(Float, nullable=True)
    mixing = Column(String(128), nullable=True)
    atmosphere = Column(String(128), nullable=True)
    # ----------------------------------------
    starting_material_1 = relationship("Chemical", primaryjoin="foreign(ExperimentDetails.startingmat_1_id) == remote(Chemical.chem_id)")

# ------------------- Plate Designer Models ------------------- #

class PlateDesign(Base):
    __tablename__ = "plate_designs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    is_template = Column(Integer, default=0) # 0 or 1
    
    # Core data: list of assignment layers
    # Each layer: { scope: 'plate'|'row'|'col'|'well', target: 'A'|'1'|'A1'|'all', role: 'solvent', data: {...}, locked: bool }
    assignments = Column(JSON, default=list)
    
    # Metadata: { eln_id, atmosphere, mixing, wavelength, tech, scale, concentration }
    plate_metadata = Column(JSON, default=dict)

    # Dimensions: 96, 48, 24
    plate_type = Column(String, default="96")

# ------------------- Screenings Module Models ------------------- #

class ScreeningPlateDesign(Base):
    __tablename__ = "screening_plate_designs"
    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), unique=True, nullable=False, index=True)
    dimensions = Column(JSON, nullable=False, default=dict)         # {"rows": 4, "columns": 6}
    global_components = Column(JSON, nullable=False, default=list)  # [{name, chem_id, role, ...}]
    axes = Column(JSON, nullable=False, default=dict)               # {"rows":[...], "columns":[...]}
    wells = Column(JSON, nullable=False, default=dict)              # {"A1": {"components": [...]}, ...}
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Screening(Base):
    __tablename__ = "screenings"
    eln_id = Column(String(128), primary_key=True)  # e.g. EXP-001
    project_name = Column(String(255), nullable=True)
    project_id = Column(String(128), nullable=True, index=True)
    theme_number = Column(String(128), nullable=True, index=True)
    date = Column(DateTime, nullable=True)
    user = Column(String(255), nullable=True)
    scale = Column(String(64), nullable=True)
    is_photochemistry = Column(Boolean, nullable=False, default=False)
    wavelength_nm = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="Planning", index=True)
    plate_design_id = Column(String(64), ForeignKey("screening_plate_designs.id"), nullable=True, index=True)
    manual_metadata = Column(JSON, nullable=False, default=dict)
    eln_stmat_data = Column(JSON, nullable=False, default=list)
    eln_product_data = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    plate_design = relationship("ScreeningPlateDesign", lazy="joined")


# ------------------- Project Process Management (PPM) Models ------------------- #

class ProcessingJob(Base):
    """One row per PDF upload. Tracks pipeline lifecycle."""
    __tablename__ = "ppm_processing_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, index=True, nullable=False)        # UUID string
    filename = Column(String(512), nullable=False)
    upload_ts = Column(DateTime, server_default=func.now(), nullable=False)
    uploader = Column(String(128), nullable=True)                               # future auth integration
    status = Column(String(32), nullable=False, default="pending")             # pending/processing/done/error/review
    error_msg = Column(Text, nullable=True)
    week_date = Column(String(32), nullable=True)                               # ISO week date string, e.g. "2026-W11"
    flagged_for_review = Column(Boolean, default=False)

    records = relationship("MoleculeStatus", backref="job", cascade="all, delete-orphan")


class MoleculeStatus(Base):
    """Core output record: one row per molecule per Proposal page detected."""
    __tablename__ = "ppm_molecule_statuses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("ppm_processing_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String(64), nullable=False, index=True)
    theme_id = Column(String(64), nullable=False, index=True)
    molecule_id = Column(String(128), nullable=False)
    status = Column(String(64), nullable=False)                                 # In plan / In progress / Obtained / Delivered / On hold / Cancelled/Stopped
    week_date = Column(String(32), nullable=True)                               # ISO date string, e.g. "2026-03-17"
    page_number = Column(Integer, nullable=True)
    structure_img = Column(Text, nullable=True)                                 # base64 PNG of 2D chemical structure extracted from PDF

    __table_args__ = (UniqueConstraint("job_id", "molecule_id", "page_number", name="uq_ppm_job_mol_page"),)


class ProjectTeamMember(Base):
    """Manual roster: which team members belong to a project."""
    __tablename__ = "ppm_team_members"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(64), nullable=False, index=True)
    member_name = Column(String(128), nullable=False)

    __table_args__ = (UniqueConstraint("project_id", "member_name", name="uq_ppm_project_member"),)


class MoleculeSmiles(Base):
    """User-entered SMILES for a molecule, plus the RDKit-generated 2D structure PNG."""
    __tablename__ = "ppm_molecule_smiles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(64), nullable=False, index=True)
    molecule_id = Column(String(128), nullable=False)
    smiles = Column(Text, nullable=False)
    structure_img = Column(Text, nullable=True)   # base64 PNG rendered by RDKit
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("project_id", "molecule_id", name="uq_ppm_mol_smiles"),)
