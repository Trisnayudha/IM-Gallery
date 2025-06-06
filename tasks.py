import os
import uuid
from PIL import Image, ImageEnhance
import boto3
from botocore.client import Config as BotoConfig
from celery_worker import celery
from models import db, Photo
from config import Config

# Inisialisasi boto3 client untuk Contabo (S3-compatible)
s3_client = boto3.client(
    "s3",
    aws_access_key_id=Config.CONTABO_S3_KEY,
    aws_secret_access_key=Config.CONTABO_S3_SECRET,
    endpoint_url=Config.CONTABO_S3_ENDPOINT,
    config=BotoConfig(s3={'addressing_style': 'path'}),
    region_name=Config.CONTABO_S3_REGION,
)

# Folder lokal tempat foto disimpan sementara
BASE_UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
ORIGINAL_FOLDER = os.path.join(BASE_UPLOAD_FOLDER, "original")
MEDIUM_FOLDER = os.path.join(BASE_UPLOAD_FOLDER, "processed", "medium")
LOW_FOLDER = os.path.join(BASE_UPLOAD_FOLDER, "processed", "low")
WATERMARK_PATH = os.path.join(os.getcwd(), "static", "watermark.jpeg")  # watermark.jpeg

def add_watermark(input_path, output_path, max_width, quality):
    """
    Resize gambar ke max_width sambil menjaga aspect ratio, lalu tambah watermark di sudut kanan bawah.
    Simpan ke output_path dengan kompresi JPEG quality tertentu.
    """
    img = Image.open(input_path).convert("RGBA")
    # Resize
    width_percent = max_width / float(img.size[0])
    height_size = int((float(img.size[1]) * float(width_percent)))
    img = img.resize((max_width, height_size), Image.LANCZOS)

    # Buka watermark
    watermark = Image.open(WATERMARK_PATH).convert("RGBA")
    # Skala watermark sesuai ukuran (misal 15% lebar foto)
    wm_ratio = 0.15
    wm_width = int(img.size[0] * wm_ratio)
    wm_h = int((wm_width / float(watermark.size[0])) * watermark.size[1])
    watermark = watermark.resize((wm_width, wm_h), Image.ANTIALIAS)

    # Atur opacity watermark (misal 60%)
    alpha = watermark.split()[3]
    alpha = ImageEnhance.Brightness(alpha).enhance(0.6)
    watermark.putalpha(alpha)

    # Tempel watermark di sudut kanan bawah (padding 10px)
    pos_x = img.size[0] - watermark.size[0] - 10
    pos_y = img.size[1] - watermark.size[1] - 10
    img.paste(watermark, (pos_x, pos_y), watermark)

    # Ubah kembali ke RGB & simpan di JPEG
    img = img.convert("RGB")
    img.save(output_path, format="JPEG", quality=quality)

@celery.task(bind=True)
def process_and_upload(self, photo_id):
    """
    Task Celery untuk: upload original, buat versi medium & low dengan watermark, lalu upload semuanya.
    """
    # Ambil data dari DB
    photo = Photo.query.get(photo_id)
    if not photo:
        return

    try:
        # Update status processing
        photo.status = "processing"
        db.session.commit()

        # Path lokal original
        orig_filename = photo.filename_original  # misal: 'uuid1234.jpg'
        local_orig_path = os.path.join(ORIGINAL_FOLDER, orig_filename)

        # 1. Upload ORIGINAL ke Contabo
        key_original = f"original/{orig_filename}"
        with open(local_orig_path, "rb") as f:
            s3_client.upload_fileobj(f, Config.CONTABO_S3_BUCKET, key_original)
        url_original = f"{Config.CONTABO_S3_ENDPOINT}/{Config.CONTABO_S3_BUCKET}/{key_original}"

        # 2. Buat dan upload versi MEDIUM
        medium_filename = f"medium_{orig_filename}"
        local_medium_path = os.path.join(MEDIUM_FOLDER, medium_filename)
        add_watermark(local_orig_path, local_medium_path, max_width=1920, quality=75)
        key_medium = f"watermark/medium/{medium_filename}"
        with open(local_medium_path, "rb") as f:
            s3_client.upload_fileobj(f, Config.CONTABO_S3_BUCKET, key_medium)
        url_medium = f"{Config.CONTABO_S3_ENDPOINT}/{Config.CONTABO_S3_BUCKET}/{key_medium}"

        # 3. Buat dan upload versi LOW
        low_filename = f"low_{orig_filename}"
        local_low_path = os.path.join(LOW_FOLDER, low_filename)
        add_watermark(local_orig_path, local_low_path, max_width=800, quality=60)
        key_low = f"watermark/low/{low_filename}"
        with open(local_low_path, "rb") as f:
            s3_client.upload_fileobj(f, Config.CONTABO_S3_BUCKET, key_low)
        url_low = f"{Config.CONTABO_S3_ENDPOINT}/{Config.CONTABO_S3_BUCKET}/{key_low}"

        # 4. Update DB dengan URL dan status done
        photo.url_original = url_original
        photo.url_medium = url_medium
        photo.url_low = url_low
        photo.status = "done"
        db.session.commit()

        # Hapus file lokal (opsional)
        os.remove(local_orig_path)
        os.remove(local_medium_path)
        os.remove(local_low_path)

        return {"status": "success", "photo_id": photo_id}
    except Exception as e:
        # Jika terjadi error, set status failed
        photo.status = "failed"
        db.session.commit()
        raise self.retry(exc=e, countdown=30, max_retries=3)
