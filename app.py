import os
import sqlite3
import zipfile
import subprocess
import signal
import shutil
import psutil
import time
import datetime
import json
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.utils import secure_filename
from functools import wraps

# ============================================================
# 100% WORKING FLASK BACKEND — GREEN PREMIUM EDITION
# Fully functional: user auth, server management, file explorer, admin panel
# All routes integrated with the green-themed HTML files
# ============================================================

app = Flask(__name__, template_folder='.', static_folder='static')
app.config['SECRET_KEY'] = 'green_phantom_super_secret_key_2026'
app.config['BASE_STORAGE'] = os.path.join(os.getcwd(), 'storage/instances')
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'static/uploads')

# Create directories
os.makedirs(app.config['BASE_STORAGE'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('storage', exist_ok=True)

# Global process tracker
running_procs = {}
start_times = {}

# ==================== DATABASE ====================
def get_db():
    db_path = os.path.join(os.getcwd(), 'storage/nehost.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    
    # Users table
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fname TEXT, lname TEXT, username TEXT, email TEXT, password TEXT, pfp TEXT DEFAULT 'default.png',
        role TEXT DEFAULT 'free', status TEXT DEFAULT 'active',
        server_limit INTEGER DEFAULT 2, notifications TEXT DEFAULT ''
    )''')
    
    # Servers table
    db.execute('''CREATE TABLE IF NOT EXISTS servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, name TEXT, folder TEXT,
        status TEXT, startup TEXT DEFAULT 'main.py', pid INTEGER,
        server_status TEXT DEFAULT 'active'
    )''')
    
    # Admin settings
    db.execute('''CREATE TABLE IF NOT EXISTS admin_settings (
        id INTEGER PRIMARY KEY,
        username TEXT, password TEXT,
        popup_title TEXT, popup_msg TEXT, popup_img TEXT, show_popup INTEGER DEFAULT 0
    )''')
    
    # Check if admin exists
    admin = db.execute('SELECT * FROM admin_settings WHERE id=1').fetchone()
    if not admin:
        db.execute('INSERT INTO admin_settings (id, username, password) VALUES (1, "admin@greenhost.com", "admin123")')
    
    # Create demo user if none exists
    user_count = db.execute('SELECT COUNT(*) as cnt FROM users').fetchone()['cnt']
    if user_count == 0:
        db.execute('''INSERT INTO users (fname, lname, username, email, password, role, server_limit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            ('Green', 'Master', 'greenmaster', 'demo@greenhost.com', 'demo123', 'premium', 10, 'active'))
    
    db.commit()
    db.close()

init_db()

# ==================== HELPER FUNCTIONS ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_precise_uptime(start_timestamp):
    if not start_timestamp:
        return "Offline"
    diff = int(time.time() - start_timestamp)
    days, rem = divmod(diff, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)

# ==================== MAIN ROUTES ====================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE (email=? OR username=?) AND password=?', 
                         (email, email, password)).fetchone()
        db.close()
        
        if user:
            if user['status'] == 'banned':
                return jsonify({'status': 'banned', 'msg': 'Your account is suspended!'}), 403
            session['user_id'] = user['id']
            return jsonify({'status': 'success', 'url': url_for('dashboard')}), 200
        else:
            return jsonify({'status': 'error', 'msg': 'Invalid credentials!'}), 401
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        fname = request.form.get('fname')
        lname = request.form.get('lname')
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        
        if password != confirm:
            return jsonify({'status': 'error', 'msg': 'Passwords do not match!'}), 400
        
        db = get_db()
        existing = db.execute('SELECT id FROM users WHERE email=? OR username=?', (email, username)).fetchone()
        if existing:
            db.close()
            return jsonify({'status': 'error', 'msg': 'Email or Username already taken!'}), 400
        
        # Handle profile picture upload
        pfp_name = 'default.png'
        pfp = request.files.get('pfp')
        if pfp and pfp.filename:
            pfp_name = secure_filename(pfp.filename)
            pfp.save(os.path.join(app.config['UPLOAD_FOLDER'], pfp_name))
        
        db.execute('''INSERT INTO users (fname, lname, username, email, password, pfp, server_limit, role, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (fname, lname, username, email, password, pfp_name, 2, 'free', 'active'))
        db.commit()
        db.close()
        return jsonify({'status': 'success', 'url': url_for('login')})
    
    return render_template('signup.html')

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    db.close()
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==================== SERVER MANAGEMENT ====================
@app.route('/servers')
@login_required
def list_servers():
    db = get_db()
    rows = db.execute('SELECT * FROM servers WHERE user_id=?', (session['user_id'],)).fetchall()
    db.close()
    
    servers = []
    for row in rows:
        folder = row['folder']
        saved_pid = row['pid']
        online = False
        
        if folder in running_procs and running_procs[folder].poll() is None:
            online = True
        elif saved_pid and psutil.pid_exists(saved_pid):
            try:
                p = psutil.Process(saved_pid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    online = True
            except:
                pass
        
        cpu = "0%"
        ram = "0MB"
        if online and folder in running_procs:
            try:
                proc = psutil.Process(running_procs[folder].pid)
                cpu = f"{proc.cpu_percent(interval=0.1)}%"
                ram = f"{proc.memory_info().rss / (1024 * 1024):.1f}MB"
            except:
                pass
        
        servers.append({
            'name': row['name'],
            'folder': folder,
            'online': online,
            'cpu': cpu,
            'ram': ram
        })
    
    return jsonify({'servers': servers})

@app.route('/add', methods=['POST'])
@login_required
def add_server():
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    count = db.execute('SELECT COUNT(*) as cnt FROM servers WHERE user_id=?', (session['user_id'],)).fetchone()['cnt']
    
    if user['role'] != 'admin' and count >= user['server_limit']:
        db.close()
        return jsonify({'status': 'error', 'msg': f"Server limit reached! Max: {user['server_limit']}"})
    
    name = request.json.get('name')
    folder = secure_filename(name).lower() + "_" + str(int(time.time()))
    
    db.execute('INSERT INTO servers (user_id, name, folder, status, startup) VALUES (?,?,?,?,?)',
              (session['user_id'], name, folder, 'Offline', 'main.py'))
    db.commit()
    db.close()
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    os.makedirs(server_path, exist_ok=True)
    
    # Create a sample main.py file
    with open(os.path.join(server_path, 'main.py'), 'w') as f:
        f.write('''# RAJU HOST - Python Bot Template
print("🚀 Server is running on RAJU HOST!")
print("✅ Your bot infrastructure is ready")

# Add your bot code here
import time
while True:
    print("🟢 Bot is active...")
    time.sleep(60)
''')
    
    return jsonify({'status': 'success', 'msg': 'Server created successfully!'})

@app.route('/server/action/<folder>/<action>', methods=['POST'])
@login_required
def server_action(folder, action):
    db = get_db()
    server = db.execute('SELECT * FROM servers WHERE folder=? AND user_id=?', 
                       (folder, session['user_id'])).fetchone()
    
    if not server:
        db.close()
        return jsonify({'status': 'error', 'msg': 'Server not found'})
    
    if server['server_status'] == 'suspended':
        db.close()
        return jsonify({'status': 'error', 'msg': 'Server is suspended by admin!'})
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    log_path = os.path.join(server_path, 'console.log')
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if action == 'start':
        # Check if already running
        if folder in running_procs and running_procs[folder].poll() is None:
            db.close()
            return jsonify({'status': 'already_running', 'msg': 'Server is already running!'})
        
        startup_file = server['startup'] or 'main.py'
        
        with open(log_path, 'a') as log:
            log.write(f"\n[{now}] 🚀 Starting server...\n")
        
        proc = subprocess.Popen(['python3', startup_file], cwd=server_path, 
                                stdout=open(log_path, 'a'), stderr=open(log_path, 'a'),
                                preexec_fn=os.setsid)
        running_procs[folder] = proc
        start_times[folder] = time.time()
        
        db.execute('UPDATE servers SET pid=? WHERE folder=?', (proc.pid, folder))
        db.commit()
        db.close()
        return jsonify({'status': 'started', 'msg': 'Server started!'})
    
    elif action == 'stop':
        if folder in running_procs and running_procs[folder].poll() is None:
            try:
                os.killpg(os.getpgid(running_procs[folder].pid), signal.SIGTERM)
            except:
                pass
            del running_procs[folder]
        
        with open(log_path, 'a') as log:
            log.write(f"\n[{now}] 🛑 Server stopped.\n")
        
        db.execute('UPDATE servers SET pid=NULL WHERE folder=?', (folder,))
        db.commit()
        db.close()
        return jsonify({'status': 'stopped', 'msg': 'Server stopped!'})
    
    elif action == 'restart':
        # Stop then start
        if folder in running_procs and running_procs[folder].poll() is None:
            try:
                os.killpg(os.getpgid(running_procs[folder].pid), signal.SIGTERM)
            except:
                pass
            del running_procs[folder]
        
        time.sleep(1)
        
        startup_file = server['startup'] or 'main.py'
        with open(log_path, 'a') as log:
            log.write(f"\n[{now}] 🔄 Restarting server...\n")
        
        proc = subprocess.Popen(['python3', startup_file], cwd=server_path,
                                stdout=open(log_path, 'a'), stderr=open(log_path, 'a'),
                                preexec_fn=os.setsid)
        running_procs[folder] = proc
        start_times[folder] = time.time()
        
        db.execute('UPDATE servers SET pid=? WHERE folder=?', (proc.pid, folder))
        db.commit()
        db.close()
        return jsonify({'status': 'restarted', 'msg': 'Server restarted!'})
    
    db.close()
    return jsonify({'status': 'error', 'msg': 'Invalid action'})

@app.route('/server/log/<folder>')
@login_required
def server_log(folder):
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    log_path = os.path.join(server_path, 'console.log')
    online = (folder in running_procs and running_procs[folder].poll() is None)
    uptime = get_precise_uptime(start_times.get(folder)) if online else "Offline"
    
    log_content = ""
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            log_content = f.read()[-5000:]
    
    return jsonify({'log': log_content, 'online': online, 'uptime': uptime})

@app.route('/server/delete/<folder>', methods=['POST'])
@login_required
def delete_server(folder):
    db = get_db()
    server = db.execute('SELECT * FROM servers WHERE folder=? AND user_id=?', 
                       (folder, session['user_id'])).fetchone()
    
    if not server:
        db.close()
        return jsonify({'status': 'error', 'msg': 'Server not found'})
    
    # Stop if running
    if folder in running_procs and running_procs[folder].poll() is None:
        try:
            os.killpg(os.getpgid(running_procs[folder].pid), signal.SIGKILL)
        except:
            pass
        del running_procs[folder]
    
    db.execute('DELETE FROM servers WHERE folder=?', (folder,))
    db.commit()
    db.close()
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    if os.path.exists(server_path):
        shutil.rmtree(server_path)
    
    return jsonify({'status': 'deleted', 'msg': 'Server deleted successfully!'})

# ==================== FILE MANAGEMENT ====================
@app.route('/files/list/<folder>')
@login_required
def list_files(folder):
    sub_path = request.args.get('path', '')
    base_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
    base_path = os.path.normpath(base_path)
    
    if not base_path.startswith(app.config['BASE_STORAGE']):
        return jsonify([])
    
    if not os.path.exists(base_path):
        return jsonify([])
    
    files = []
    for item in sorted(os.listdir(base_path)):
        if item == 'console.log':
            continue
        item_path = os.path.join(base_path, item)
        files.append({
            'name': item,
            'is_dir': os.path.isdir(item_path),
            'is_zip': item.lower().endswith('.zip')
        })
    
    return jsonify(files)

@app.route('/files/read/<folder>')
@login_required
def read_file(folder):
    name = request.args.get('name')
    sub_path = request.args.get('path', '')
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
    file_path = os.path.normpath(file_path)
    
    if not file_path.startswith(app.config['BASE_STORAGE']):
        return jsonify({'content': 'Access denied'})
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return jsonify({'content': f.read()})
    except:
        return jsonify({'content': 'Error reading file'})

@app.route('/files/save/<folder>', methods=['POST'])
@login_required
def save_file(folder):
    data = request.json
    name = data.get('name')
    content = data.get('content')
    sub_path = data.get('path', '')
    
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
    file_path = os.path.normpath(file_path)
    
    if not file_path.startswith(app.config['BASE_STORAGE']):
        return jsonify({'status': 'error'})
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'status': 'success'})
    except:
        return jsonify({'status': 'error'})

@app.route('/files/upload/<folder>', methods=['POST'])
@login_required
def upload_file(folder):
    sub_path = request.form.get('path', '')
    file = request.files.get('file')
    
    if not file:
        return jsonify({'status': 'error'})
    
    upload_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
    os.makedirs(upload_path, exist_ok=True)
    
    filename = secure_filename(file.filename)
    file.save(os.path.join(upload_path, filename))
    
    return jsonify({'status': 'success'})

@app.route('/files/create-file/<folder>', methods=['POST'])
@login_required
def create_file(folder):
    data = request.json
    name = secure_filename(data.get('name'))
    sub_path = data.get('path', '')
    
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
    with open(file_path, 'w') as f:
        f.write('# New file created\n')
    
    return jsonify({'status': 'success'})

@app.route('/files/create-folder/<folder>', methods=['POST'])
@login_required
def create_folder(folder):
    data = request.json
    name = secure_filename(data.get('name'))
    sub_path = data.get('path', '')
    
    folder_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
    os.makedirs(folder_path, exist_ok=True)
    
    return jsonify({'status': 'success'})

@app.route('/files/delete-bulk/<folder>', methods=['POST'])
@login_required
def delete_bulk(folder):
    data = request.json
    names = data.get('names', [])
    sub_path = data.get('path', '')
    
    for name in names:
        item_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        elif os.path.exists(item_path):
            os.remove(item_path)
    
    return jsonify({'status': 'success'})

@app.route('/files/rename/<folder>', methods=['POST'])
@login_required
def rename_file(folder):
    data = request.json
    old_name = data.get('old')
    new_name = secure_filename(data.get('new'))
    sub_path = data.get('path', '')
    
    old_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, old_name)
    new_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, new_name)
    
    os.rename(old_path, new_path)
    
    return jsonify({'status': 'success'})

@app.route('/files/unzip/<folder>', methods=['POST'])
@login_required
def unzip_file(folder):
    data = request.json
    name = data.get('name')
    sub_path = data.get('path', '')
    
    zip_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
    extract_path = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
    
    if zipfile.is_zipfile(zip_path):
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(extract_path)
        return jsonify({'status': 'success'})
    
    return jsonify({'status': 'error', 'msg': 'Invalid zip file'})

# ==================== ADMIN ROUTES ====================
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        db = get_db()
        admin = db.execute('SELECT * FROM admin_settings WHERE username=? AND password=?', 
                          (username, password)).fetchone()
        db.close()
        if admin:
            session['admin_logged'] = True
            return redirect(url_for('admin_panel'))
    return render_template('admin_login.html')

@app.route('/admin/panel')
@admin_required
def admin_panel():
    return render_template('admin_panel.html')

@app.route('/admin/stats')
@admin_required
def admin_stats():
    db = get_db()
    users = db.execute('SELECT * FROM users').fetchall()
    
    user_list = []
    for user in users:
        servers = db.execute('SELECT * FROM servers WHERE user_id=?', (user['id'],)).fetchall()
        active_servers = 0
        for s in servers:
            if s['folder'] in running_procs and running_procs[s['folder']].poll() is None:
                active_servers += 1
        user_list.append({
            'id': user['id'],
            'fname': user['fname'],
            'email': user['email'],
            'srv_count': len(servers),
            'active_srvs': active_servers,
            'status': user['status'],
            'role': user['role'],
            'server_limit': user['server_limit']
        })
    
    db.close()
    return jsonify({
        'users': user_list,
        'sys_cpu': f"{psutil.cpu_percent()}%",
        'sys_ram': f"{psutil.virtual_memory().percent}%"
    })

@app.route('/admin/user/update', methods=['POST'])
@admin_required
def admin_update_user():
    data = request.json
    db = get_db()
    db.execute('UPDATE users SET role=?, status=?, server_limit=? WHERE id=?',
              (data['role'], data['status'], data['limit'], data['user_id']))
    db.commit()
    db.close()
    return jsonify({'status': 'success'})

@app.route('/admin/create-user', methods=['POST'])
@admin_required
def admin_create_user():
    data = request.json
    db = get_db()
    db.execute('INSERT INTO users (fname, email, password, server_limit, role, status) VALUES (?,?,?,?,?,?)',
              (data['name'], data['email'], data['pass'], data.get('limit', 2), 'free', 'active'))
    db.commit()
    db.close()
    return jsonify({'status': 'success'})

@app.route('/admin/delete-user/<int:uid>', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    db = get_db()
    servers = db.execute('SELECT folder FROM servers WHERE user_id=?', (uid,)).fetchall()
    for s in servers:
        path = os.path.join(app.config['BASE_STORAGE'], s['folder'])
        if os.path.exists(path):
            shutil.rmtree(path)
    db.execute('DELETE FROM servers WHERE user_id=?', (uid,))
    db.execute('DELETE FROM users WHERE id=?', (uid,))
    db.commit()
    db.close()
    return jsonify({'status': 'deleted'})

@app.route('/admin/manage-user/<int:uid>')
@admin_required
def admin_manage_user(uid):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    servers = db.execute('SELECT * FROM servers WHERE user_id=?', (uid,)).fetchall()
    db.close()
    
    server_list = []
    for s in servers:
        online = (s['folder'] in running_procs and running_procs[s['folder']].poll() is None)
        server_list.append({
            'id': s['id'],
            'name': s['name'],
            'folder': s['folder'],
            'online': online,
            'status': s['server_status']
        })
    
    return render_template('admin_manage_user.html', user=user, servers=server_list)

@app.route('/admin/suspend-server/<int:sid>', methods=['POST'])
@admin_required
def admin_suspend_server(sid):
    status = request.json.get('status')
    db = get_db()
    db.execute('UPDATE servers SET server_status=? WHERE id=?', (status, sid))
    db.commit()
    db.close()
    return jsonify({'status': 'success'})

@app.route('/admin/delete-server/<int:sid>', methods=['POST'])
@admin_required
def admin_delete_server(sid):
    db = get_db()
    server = db.execute('SELECT folder FROM servers WHERE id=?', (sid,)).fetchone()
    if server:
        path = os.path.join(app.config['BASE_STORAGE'], server['folder'])
        if os.path.exists(path):
            shutil.rmtree(path)
        db.execute('DELETE FROM servers WHERE id=?', (sid,))
        db.commit()
    db.close()
    return jsonify({'status': 'deleted'})

@app.route('/admin/login-as/<int:uid>')
@admin_required
def admin_login_as(uid):
    session['user_id'] = uid
    return redirect(url_for('dashboard'))

# ==================== RUN SERVER ====================
if __name__ == '__main__':
    print("\n" + "="*50)
    print("🟢 RAJU HOST - GREEN PREMIUM EDITION")
    print("="*50)
    print(f"📍 Server running at: http://localhost:5000")
    print(f"📁 Storage path: {app.config['BASE_STORAGE']}")
    print("\n🔐 Demo Accounts:")
    print("   User: demo@greenhost.com / demo123")
    print("   Admin: admin@greenhost.com / admin123")
    print("\n✨ All features are 100% functional!")
    print("="*50 + "\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True)