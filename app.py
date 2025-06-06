CONTABO_BASE_FOLDER = "gallery"
import os
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from config import Config
from models import db, Photo
from models import Folder
from celery_worker import celery
from tasks import process_and_upload
import traceback
from flask import current_app

import subprocess

def mirror_to_contabo(local_path: str, subfolder: str, filename: str):
    """
    Mirror a local file to Contabo via rclone.
    local_path: path to the local file.
    subfolder: the Contabo remote subfolder (e.g., "original", "watermark/medium", "watermark/low").
    filename: name of the file (e.g., "abc123.jpg").
    """
    bucket = os.getenv("CONTABO_BUCKET", "indonesiaminer")
    base_folder = CONTABO_BASE_FOLDER
    # Construct key as <bucket>/<base_folder>/<subfolder>/<filename>
    remote_key = f"{bucket}/{base_folder}/{subfolder}/{filename}"
    # Execute rclone copyto: local file to remote bucket
    subprocess.run(
        ["rclone", "copyto", local_path, f"contabo:{remote_key}"],
        check=True
    )

import boto3
from botocore.client import Config as BotoConfig
from PIL import Image, ImageEnhance, ImageOps

# Initialize S3 client for Contabo
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("CONTABO_KEY"),
    aws_secret_access_key=os.getenv("CONTABO_SECRET"),
    endpoint_url=os.getenv("CONTABO_ENDPOINT"),
    config=BotoConfig(signature_version='s3v4', s3={'addressing_style': 'path'}),
    region_name=os.getenv("CONTABO_REGION", "us-east-1"),
)

# Function to add watermark and resize
def add_watermark(input_path: str, output_path: str, max_width: int, quality: int):
    img = Image.open(input_path)
    img = ImageOps.exif_transpose(img).convert("RGBA")
    ratio = max_width / float(img.size[0])
    new_h = int(img.size[1] * ratio)
    img = img.resize((max_width, new_h), Image.LANCZOS)

    watermark = Image.open(os.path.join(os.getcwd(), "static", "watermark.png")).convert("RGBA")
    print(f"Adding watermark: img size={img.size}, watermark size (before resize)={watermark.size}")
    wm_ratio = 0.15
    wm_w = int(img.size[0] * wm_ratio)
    wm_h = int((wm_w / watermark.size[0]) * watermark.size[1])
    watermark = watermark.resize((wm_w, wm_h), Image.LANCZOS)
    print(f"Adding watermark: img size={img.size}, watermark size (after resize)={watermark.size}")

    alpha = watermark.split()[3]
    alpha = ImageEnhance.Brightness(alpha).enhance(0.6)
    watermark.putalpha(alpha)

    pos_x = int((img.size[0] - watermark.size[0]) / 2)
    pos_y = int((img.size[1] - watermark.size[1]) / 2)
    img.paste(watermark, (pos_x, pos_y), watermark)

    img = img.convert("RGB")
    img.save(output_path, format="png", quality=quality, optimize=True)

# Define folder paths
UPLOAD_FOLDER = Config().__dict__.get("UPLOAD_FOLDER", "uploads")
ORIGINAL_FOLDER = os.path.join(UPLOAD_FOLDER, "original")
MEDIUM_FOLDER = os.path.join(UPLOAD_FOLDER, "processed", "medium")
LOW_FOLDER = os.path.join(UPLOAD_FOLDER, "processed", "low")

app = Flask(__name__)
app.config.from_object(Config)

# Inisialisasi database
db.init_app(app)

# Pastikan folder upload ada
os.makedirs(ORIGINAL_FOLDER, exist_ok=True)
os.makedirs(MEDIUM_FOLDER, exist_ok=True)
os.makedirs(LOW_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "tiff", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def index():
    return render_template("upload.html")

@app.route("/upload", methods=["POST"])
def upload():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part"}), 400

        file = request.files["file"]
        if not (file and allowed_file(file.filename)):
            return jsonify({"error": "Invalid file"}), 400

        # Retrieve dynamic folder from frontend
        folder = request.form.get("folder", "").strip()
        if not folder:
            return jsonify({"error": "Folder name is required"}), 400

        # Find or create Folder in database
        existing_folder = Folder.query.filter_by(path=folder).first()
        if existing_folder is None:
            new_folder = Folder(path=folder)
            db.session.add(new_folder)
            db.session.commit()
            folder_obj = new_folder
        else:
            folder_obj = existing_folder

        ext = file.filename.rsplit(".", 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex}.{ext}"

        # Use folder from frontend (or fallback)
        dynamic_orig_folder = os.path.join(ORIGINAL_FOLDER, folder)
        os.makedirs(dynamic_orig_folder, exist_ok=True)
        local_orig = os.path.join(dynamic_orig_folder, secure_filename(unique_name))

        # 1) Simpan file original ke MOUNT_POINT/original/
        file.save(local_orig)

        # Mirror original to Contabo via rclone
        mirror_to_contabo(local_orig, f"original/{folder}", unique_name)

        # Simpan metadata di DB (tetap sama)
        photo = Photo(uuid=unique_name)
        photo.folder_id = folder_obj.id
        db.session.add(photo)
        db.session.commit()

        # 2) Buat versi medium & watermark
        med_name = f"medium_{unique_name}"
        dynamic_med_folder = os.path.join(MEDIUM_FOLDER, folder)
        os.makedirs(dynamic_med_folder, exist_ok=True)
        local_med = os.path.join(dynamic_med_folder, med_name)
        add_watermark(local_orig, local_med, max_width=1920, quality=75)

        # Mirror medium version to Contabo
        mirror_to_contabo(local_med, f"watermark/medium/{folder}", med_name)

        # 3) Buat versi low & watermark
        low_name = f"low_{unique_name}"
        dynamic_low_folder = os.path.join(LOW_FOLDER, folder)
        os.makedirs(dynamic_low_folder, exist_ok=True)
        local_low = os.path.join(dynamic_low_folder, low_name)
        add_watermark(local_orig, local_low, max_width=800, quality=60)

        # Mirror low version to Contabo
        mirror_to_contabo(local_low, f"watermark/low/{folder}", low_name)

        # Simpan path ke DB (sesuaikan URL-nya nanti)
        photo.original_key = f"{CONTABO_BASE_FOLDER}/original/{folder}/{unique_name}"
        photo.medium_key   = f"{CONTABO_BASE_FOLDER}/watermark/medium/{folder}/{med_name}"
        photo.low_key      = f"{CONTABO_BASE_FOLDER}/watermark/low/{folder}/{low_name}"
        db.session.commit()

        # (Opsional) Hapus file lokal temp dari folder lain, tapi karena
        # kita langsung nulis ke bucket via rclone mount, hapus sini tidak perlu.

        # 4) Bangun URL publik:
        bucket = os.getenv("CONTABO_BUCKET", "indonesiaminer")
        base_url = f"{os.getenv('CONTABO_ENDPOINT')}/{bucket}"
        return jsonify({
            "uuid": unique_name,
            "status": "done",
            "url_original": f"{base_url}/{photo.original_key}",
            "url_medium":   f"{base_url}/{photo.medium_key}",
            "url_low":      f"{base_url}/{photo.low_key}"
        }), 200

    except Exception as e:
        traceback.print_exc()
        current_app.logger.error("Upload gagal: %s", e)
        return jsonify({"error": "Processing failed", "detail": str(e)}), 500
    

@app.route("/status/<int:photo_id>", methods=["GET"])
def status(photo_id):
    """
    Untuk mengecek status processing (optional, bisa dipanggil AJAX).
    """
    photo = Photo.query.get(photo_id)
    if not photo:
        return jsonify({"error": "Not found"}), 404

    return jsonify({
        "photo_id": photo.id,
        "status": photo.status,
        "url_original": photo.url_original,
        "url_medium": photo.url_medium,
        "url_low": photo.url_low
    }), 200



# --- Folders list endpoint
@app.route("/folders", methods=["GET"])
def get_folders():
    """
    Mengembalikan daftar folder yang tersimpan di tabel Folder sebagai array JSON.
    """
    folder_objs = Folder.query.order_by(Folder.created_at.desc()).all()
    paths = [f.path for f in folder_objs]
    return jsonify(paths), 200

# --- Create folder endpoint
@app.route("/folders", methods=["POST"])
def create_folder():
    """
    Terima JSON dengan field 'path' untuk menyimpan folder baru ke database.
    """
    data = request.get_json()
    if not data or "path" not in data or not data["path"].strip():
        return jsonify({"error": "Field 'path' is required"}), 400

    folder_path = data["path"].strip()
    # Cek apakah folder sudah ada
    existing = Folder.query.filter_by(path=folder_path).first()
    if existing:
        return jsonify({"message": "Folder already exists", "path": existing.path}), 200

    # Simpan folder baru
    new_folder = Folder(path=folder_path)
    db.session.add(new_folder)
    db.session.commit()

    return jsonify({"message": "Folder created", "path": new_folder.path}), 201

# --- List images in a folder endpoint
@app.route("/images", methods=["GET"])
def get_images():
    """
    Mengembalikan daftar gambar dalam folder tertentu dengan URL original, medium, dan low.
    """
    folder = request.args.get("folder", "").strip()
    if not folder:
        return jsonify([]), 200

    # Cari folder berdasarkan path
    folder_obj = Folder.query.filter_by(path=folder).first()
    if not folder_obj:
        return jsonify([]), 200

    # Ambil semua foto terkait folder tersebut
    photos = Photo.query.filter_by(folder_id=folder_obj.id).all()

    # Bangun base URL untuk konten S3/Contabo
    bucket = os.getenv("CONTABO_BUCKET", "indonesiaminer")
    base_url = f"{os.getenv('CONTABO_ENDPOINT')}/"

    result = []
    for p in photos:
        result.append({
            "id": p.id,
            "original_key": f"{base_url}/{p.original_key}",
            "medium_key": f"{base_url}/{p.medium_key}",
            "low_key": f"{base_url}/{p.low_key}"
        })
    return jsonify(result), 200

# --- Delete single image endpoint
@app.route("/image/<int:photo_id>", methods=["DELETE"])
def delete_image(photo_id):
    """
    Menghapus satu foto baik dari database maupun bucket S3/Contabo.
    """
    photo = Photo.query.get(photo_id)
    if not photo:
        return jsonify({"error": "Not found"}), 404

    # Coba hapus objek dari Contabo/S3 (opsional jika bucket terisi)
    try:
        bucket = os.getenv("CONTABO_BUCKET", "indonesiaminer")
        s3_client.delete_object(Bucket=bucket, Key=photo.original_key)
        s3_client.delete_object(Bucket=bucket, Key=photo.medium_key)
        s3_client.delete_object(Bucket=bucket, Key=photo.low_key)
    except Exception as e:
        current_app.logger.error("Gagal menghapus dari Contabo: %s", e)

    # Hapus dari database
    db.session.delete(photo)
    db.session.commit()
    return jsonify({"message": "Deleted"}), 200

if __name__ == "__main__":
    # Jalankan Flask
    # Pastikan Anda telah membuat tabel di DB (db.create_all()) sebelum server start
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
