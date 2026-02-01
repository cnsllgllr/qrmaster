import os
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.utils import secure_filename
import time

app = Flask(__name__)
CORS(app) # Enable CORS for frontend communication

# Configuration
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'qr_master.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB Max upload

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# --- Models ---
class QRRecord(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    batch_id = db.Column(db.String(36), nullable=False, index=True) # Added index for faster deletions
    created_at = db.Column(db.Integer, nullable=False) # Timestamp
    report_title = db.Column(db.String(200), nullable=True)
    report_note = db.Column(db.Text, nullable=True)
    report_file = db.Column(db.String(300), nullable=True) # Path/URL to file
    file_name = db.Column(db.String(200), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'batchId': self.batch_id,
            'createdAt': self.created_at,
            'reportTitle': self.report_title,
            'reportNote': self.report_note,
            'reportFile': f"http://localhost:5000/uploads/{self.report_file}" if self.report_file else None,
            'fileName': self.file_name
        }

# --- Routes ---

# 1. Init DB
with app.app_context():
    db.create_all()

# 2. Serve Uploaded Files
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# 3. Auth (Simple Mock for now to match existing frontend logic)
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    # Hardcoded for now, can be moved to DB later
    if username == 'admin' and password == '1234':
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

# 4. GET All QRs
@app.route('/api/qrs', methods=['GET'])
def get_qrs():
    # Optimization: Use yield_per if data is massive, but for now standard fetch is okay
    # Limiting fields in query could also be an optimization if needed later
    records = QRRecord.query.order_by(QRRecord.created_at.desc()).all()
    return jsonify([r.to_dict() for r in records])

# 5. GET Single QR
@app.route('/api/qrs/<id>', methods=['GET'])
def get_qr(id):
    record = QRRecord.query.get(id)
    if record:
        return jsonify(record.to_dict())
    return jsonify({'error': 'Not found'}), 404

# 6. Create Batch (POST)
@app.route('/api/qrs/batch', methods=['POST'])
def create_batch():
    data = request.json
    # Optimization: Use bulk_insert_mappings for massive inserts
    if len(data) > 1000:
        db.session.bulk_insert_mappings(QRRecord, [
            {
                'id': item['id'],
                'batch_id': item['batchId'],
                'created_at': item['createdAt'],
                'report_title': None,
                'report_note': None,
                'report_file': None,
                'file_name': None
            } for item in data
        ])
    else:
        for item in data:
            record = QRRecord(
                id=item['id'],
                batch_id=item['batchId'],
                created_at=item['createdAt']
            )
            db.session.add(record)
    
    db.session.commit()
    return jsonify({'success': True, 'count': len(data)}), 201

# 7. Update Record (Upload Report)
@app.route('/api/qrs/<id>', methods=['PUT'])
def update_qr(id):
    record = QRRecord.query.get(id)
    if not record:
        return jsonify({'error': 'Not found'}), 404

    # Handle Multipart Form Data (File Upload)
    if 'file' in request.files:
        file = request.files['file']
        if file.filename != '':
            filename = secure_filename(f"{id}_{int(time.time())}_{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Remove old file if exists
            if record.report_file:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], record.report_file)
                if os.path.exists(old_path):
                    os.remove(old_path)
            
            record.report_file = filename
            record.file_name = file.filename # Original name

    # Handle Text Data
    if 'reportTitle' in request.form:
        record.report_title = request.form['reportTitle']
    if 'reportNote' in request.form:
        record.report_note = request.form['reportNote']
    
    # Allow removing file via flag
    if request.form.get('removeFile') == 'true':
         if record.report_file:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], record.report_file)
                if os.path.exists(old_path):
                    os.remove(old_path)
         record.report_file = None
         record.file_name = None
         record.report_title = None
         record.report_note = None

    db.session.commit()
    return jsonify(record.to_dict())

# 8. Delete Report Only
@app.route('/api/qrs/<id>/report', methods=['DELETE'])
def delete_report(id):
    record = QRRecord.query.get(id)
    if not record:
        return jsonify({'error': 'Not found'}), 404
    
    if record.report_file:
        old_path = os.path.join(app.config['UPLOAD_FOLDER'], record.report_file)
        if os.path.exists(old_path):
            os.remove(old_path)
    
    record.report_file = None
    record.file_name = None
    record.report_title = None
    record.report_note = None
    
    db.session.commit()
    return jsonify(record.to_dict())

# 9. Bulk Delete Records (OPTIMIZED)
@app.route('/api/qrs/bulk-delete', methods=['POST'])
def delete_records():
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'success': True, 'deleted_count': 0})

    # 1. Clean up files first (Only fetch records that actually have files)
    # This prevents loading 100k objects into memory just to check if they have a file
    records_with_files = QRRecord.query.filter(QRRecord.id.in_(ids)).filter(QRRecord.report_file.isnot(None)).all()
    
    for r in records_with_files:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], r.report_file)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass # Continue even if file deletion fails

    # 2. Bulk Database Delete
    # Synchronize session not needed for massive delete usually, 'fetch' is safer for cascade but slower. 
    # For speed with 100k records, simple delete is best.
    try:
        delete_q = QRRecord.__table__.delete().where(QRRecord.id.in_(ids))
        db.session.execute(delete_q)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

    return jsonify({'success': True})

# 10. Delete Entire Batch (NEW - HIGHLY OPTIMIZED)
@app.route('/api/qrs/batch/<batch_id>', methods=['DELETE'])
def delete_batch(batch_id):
    # 1. Cleanup files for this batch
    # Only fetch records with files to save memory
    records_with_files = QRRecord.query.filter_by(batch_id=batch_id).filter(QRRecord.report_file.isnot(None)).all()
    
    for r in records_with_files:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], r.report_file)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    # 2. SQL Bulk Delete by Batch ID
    try:
        QRRecord.query.filter_by(batch_id=batch_id).delete()
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
