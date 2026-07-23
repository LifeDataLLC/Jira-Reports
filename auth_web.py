"""
auth_web.py
-----------
Login, registration, logout, and admin user management pages.

Registration rules:
- The very first account created is the admin (bootstrap).
- After that, public registration creates EMPLOYEE accounts only — the person
  must pick which developer they are and is warned the link is permanent.
- Admins create further accounts (either role) from /admin/users.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template_string, request, url_for

import auth

authbp = Blueprint("auth", __name__)

SHELL = """
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f5f7;color:#172b4d;margin:0}
 .box{max-width:420px;margin:60px auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(9,30,66,.16);padding:28px 30px}
 h1{font-size:20px;margin:0 0 4px}.sub{color:#6b778c;font-size:13px;margin-bottom:18px}
 label{display:block;font-size:12px;color:#5e6c84;margin:12px 0 3px}
 input,select{width:100%;box-sizing:border-box;padding:9px 11px;border:1px solid #dfe1e6;border-radius:6px;font-size:14px}
 input:focus,select:focus{outline:none;border-color:#1fa963;box-shadow:0 0 0 3px rgba(31,169,99,.18)}
 .btn{margin-top:18px;width:100%;background:#1fa963;color:#fff;border:none;border-radius:6px;padding:11px;font-size:14px;font-weight:600;cursor:pointer}
 .btn:hover{background:#17864e}
 .err{background:#ffebe6;color:#bf2600;border-radius:6px;padding:9px 12px;font-size:13px;margin-bottom:6px}
 .warn{background:#fff7e6;color:#974f00;border-radius:6px;padding:9px 12px;font-size:13px;margin:10px 0}
 .muted{color:#6b778c;font-size:12px;margin-top:14px}
 a{color:#17864e;text-decoration:none}
</style>
"""

LOGIN = SHELL + """
<div class="box">
  <h1>Sign in</h1>
  <div class="sub">LifeData engineering reports</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="post">
    <input type="hidden" name="next" value="{{ nxt }}">
    <label>Work email</label>
    <input name="email" type="email" autocomplete="email" required autofocus value="{{ email }}">
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password" required>
    <button class="btn" type="submit">Sign in</button>
  </form>
  <div class="muted">No account yet? <a href="/register">Create one</a>.</div>
</div>
"""

REGISTER = SHELL + """
<div class="box">
  <h1>{{ 'Create the administrator account' if first else 'Create your account' }}</h1>
  <div class="sub">{% if first %}This is the first account, so it will be the administrator.{% else %}Employees link their account to their developer.{% endif %}</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="post" id="reg">
    <label>Work email</label>
    <input name="email" type="email" autocomplete="email" required value="{{ email }}">
    <label>Password <span class="muted">(min 8 characters)</span></label>
    <input name="password" type="password" autocomplete="new-password" required>
    {% if not first %}
    <label>Which developer are you?</label>
    <select name="developer_id" id="devsel" required>
      <option value="">— select your name —</option>
      {% for d in developers %}<option value="{{ d.id }}" data-name="{{ d.name }}">{{ d.name }}</option>{% endfor %}
    </select>
    <div class="warn">Your account will be <b>permanently linked</b> to the developer you select. This will be the only developer you can view on My Day. Choose carefully.</div>
    {% else %}
    <label>Associate with a developer <span class="muted">(optional)</span></label>
    <select name="developer_id">
      <option value="">— none —</option>
      {% for d in developers %}<option value="{{ d.id }}" data-name="{{ d.name }}">{{ d.name }}</option>{% endfor %}
    </select>
    {% endif %}
    <input type="hidden" name="developer_name" id="devname">
    <button class="btn" type="submit">Create account</button>
  </form>
  <div class="muted">Already have an account? <a href="/login">Sign in</a>.</div>
</div>
<script>
 var f=document.getElementById('reg'), sel=document.getElementById('devsel')||f.querySelector('[name=developer_id]');
 f.addEventListener('submit',function(){
   var o=sel.options[sel.selectedIndex];
   document.getElementById('devname').value = o ? (o.getAttribute('data-name')||'') : '';
 });
</script>
"""


@authbp.route("/login", methods=["GET", "POST"])
def login():
    if auth.user_count() == 0:
        return redirect("/register")
    error = None
    nxt = request.args.get("next") or request.form.get("next") or "/"
    email = request.form.get("email", "")
    if request.method == "POST":
        u = auth.verify(email, request.form.get("password", ""))
        if u:
            auth.login_user(u)
            # Same-site paths only: "//evil.com" starts with "/" but browsers
            # treat it as protocol-relative → open redirect. Require exactly one
            # leading slash.
            safe = nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt
            return redirect(nxt if safe else "/")
        error = "Incorrect email or password."
    return render_template_string(LOGIN, error=error, nxt=nxt, email=email)


@authbp.route("/logout")
def logout():
    auth.logout_user()
    return redirect("/login")


CHANGE_PW = SHELL + """
<div class="box">
  <h1>{{ 'Set your password' if forced else 'Change password' }}</h1>
  <div class="sub">{% if forced %}Your account was created with a temporary password — set your own to continue.{% else %}Update the password for {{ email }}.{% endif %}</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  {% if msg %}<div class="warn" style="background:#e3fcef;color:#006644">{{ msg }}</div>{% endif %}
  <form method="post">
    {% if not forced %}
    <label>Current password</label>
    <input name="current" type="password" autocomplete="current-password" required autofocus>
    {% endif %}
    <label>New password <span class="muted">(min 8 characters)</span></label>
    <input name="new" type="password" autocomplete="new-password" required {% if forced %}autofocus{% endif %}>
    <label>Confirm new password</label>
    <input name="confirm" type="password" autocomplete="new-password" required>
    <button class="btn" type="submit">Save password</button>
  </form>
  <div class="muted">{% if forced %}<a href="/logout">Log out</a>{% else %}<a href="/">Back to the app</a>{% endif %}</div>
</div>
"""


@authbp.route("/change-password", methods=["GET", "POST"])
def change_password():
    user = auth.current_user()
    if not user:
        return redirect("/login?next=/change-password")
    forced = bool(user.get("must_change"))
    error = msg = None
    if request.method == "POST":
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        # A forced (first-login) change skips the current-password check — the user
        # just authenticated with the temp password to get here.
        if not forced and not auth.verify(user["email"], request.form.get("current", "")):
            error = "Current password is incorrect."
        elif new != confirm:
            error = "The new passwords do not match."
        else:
            try:
                auth.set_password(user["email"], new)
                if forced:
                    return redirect("/")
                msg = "Password updated."
            except auth.AuthError as e:
                error = str(e)
    return render_template_string(CHANGE_PW, forced=forced, email=user["email"],
                                  error=error, msg=msg)


@authbp.route("/register", methods=["GET", "POST"])
def register():
    first = auth.user_count() == 0
    # Public registration only creates employees (or the first-ever admin).
    developers = auth.visible_developers()
    error = None
    email = request.form.get("email", "")
    if request.method == "POST":
        role = "admin" if first else "employee"
        try:
            u = auth.create_user(
                email, request.form.get("password", ""), role,
                developer=request.form.get("developer_name") or None,
                developer_id=request.form.get("developer_id") or None)
            auth.login_user(u)
            return redirect("/")
        except auth.AuthError as e:
            error = str(e)
    return render_template_string(REGISTER, first=first, developers=developers,
                                  error=error, email=email)


# ---------------------------------------------------------------------------
# Admin: user management
# ---------------------------------------------------------------------------

USERS_TMPL = """
<h1>Users</h1>
<div class="sub">Manage accounts. Each account can be linked to a developer — change it any time from the Developer column below.</div>
{% if error %}<div class="banner" style="background:#ffebe6;border-color:#ffbdad;color:#bf2600">{{ error }}</div>{% endif %}
{% if msg %}<div class="banner" style="background:#e3fcef;border-color:#abf5d1;color:#006644">{{ msg }}</div>{% endif %}
<div class="sectionbox">
  <h2 style="margin-top:0">Add an account</h2>
  <form method="post" style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
    <input type="hidden" name="action" value="create">
    <label style="font-size:12px;color:#5e6c84">Email<br><input name="email" type="email" required style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px"></label>
    <label style="font-size:12px;color:#5e6c84">Temporary password<br><input name="password" type="text" required placeholder="min 8 chars" style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px"></label>
    <label style="font-size:12px;color:#5e6c84">Role<br>
      <select name="role" style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px">
        <option value="employee">employee</option><option value="admin">admin</option></select></label>
    <label style="font-size:12px;color:#5e6c84">Developer<br>
      <select name="developer_id" style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px">
        <option value="">— none —</option>
        {% for d in developers %}<option value="{{ d.id }}">{{ d.name }}</option>{% endfor %}</select></label>
    <button class="btn" type="submit">Create</button>
  </form>
  <p class="muted">Employees must be linked to a developer; admins may leave it blank. Give the person the temporary password — they'll be required to set their own the first time they log in.</p>
</div>
<table>
<tr><th>Email</th><th>Role</th><th>Developer</th><th>Created</th><th></th></tr>
{% for u in users %}
<tr><td>{{ u.email }}</td><td><span class="pill {{ 'bad' if u.role=='admin' else '' }}">{{ u.role }}</span></td>
<td>
  <form method="post" style="display:inline-flex;gap:5px;align-items:center">
    <input type="hidden" name="action" value="set_developer"><input type="hidden" name="email" value="{{ u.email }}">
    <select name="developer_id" style="padding:5px 7px;border:1px solid #dfe1e6;border-radius:6px;font-size:12px">
      <option value="">— none —</option>
      {% for d in all_devs %}<option value="{{ d.id }}"{{ ' selected' if (d.id==u.developer_id or (not u.developer_id and d.name==u.developer)) else '' }}>{{ d.name }}</option>{% endfor %}
    </select>
    <button class="btn-ghost" type="submit">Update</button>
  </form>
</td><td class="muted">{{ u.created_at[:10] }}</td>
<td>
<form method="post" style="display:inline" onsubmit="return confirm('Reset the password for {{ u.email }}? A new temporary password will be generated and shown once.')"><input type="hidden" name="action" value="reset"><input type="hidden" name="email" value="{{ u.email }}"><button class="btn-ghost" type="submit">Reset password</button></form>
<form method="post" style="display:inline" onsubmit="return confirm('Delete {{ u.email }}?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="email" value="{{ u.email }}"><button class="btn-ghost" type="submit">Delete</button></form>
</td></tr>
{% endfor %}
</table>
"""


@authbp.route("/admin/users", methods=["GET", "POST"])
def admin_users():
    import screens_web
    error = msg = None
    dev_by_id = {d["id"]: d["name"] for d in auth.all_developers()}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            target = request.form.get("email", "")
            cur = auth.current_user()
            if cur and target == cur["email"]:
                error = "You can't delete your own account."
            else:
                auth.delete_user(target)
                msg = f"Deleted {target}."
        elif action == "reset":
            target = request.form.get("email", "")
            if not auth.get_user(target):
                error = "Account not found."
            else:
                temp = auth.generate_temp_password()
                auth.set_password(target, temp, must_change=True)
                msg = (f"Password reset for {target}. Temporary password: {temp} — "
                       f"share it with them; they'll set their own on next login.")
        elif action == "create":
            did = request.form.get("developer_id") or None
            try:
                auth.create_user(request.form.get("email", ""), request.form.get("password", ""),
                                 request.form.get("role", "employee"),
                                 developer=dev_by_id.get(did), developer_id=did,
                                 must_change=True)  # user sets their own on first login
                msg = "Account created. The user sets their own password on first login."
            except auth.AuthError as e:
                error = str(e)
        elif action == "set_developer":
            target = request.form.get("email", "")
            did = request.form.get("developer_id") or None
            try:
                auth.set_developer(target, developer=dev_by_id.get(did), developer_id=did)
                msg = (f"Linked {target} to {dev_by_id[did]}." if did
                       else f"Unlinked {target} from any developer.")
            except auth.AuthError as e:
                error = str(e)
    return screens_web.page(USERS_TMPL, active="/settings", users=auth.list_users(),
                            developers=auth.visible_developers(),
                            all_devs=auth.all_developers(), error=error, msg=msg)
