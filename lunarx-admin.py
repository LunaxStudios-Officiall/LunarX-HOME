#!/usr/bin/env python3
"""Root-owned, allow-listed broker for LunarX Home administration."""
from __future__ import annotations
import ctypes, ctypes.util, grp, json, os, posixpath, pwd, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path, PurePosixPath
from typing import Any

APP_ROOT=Path(os.environ.get("LUNARX_APP_ROOT","/opt/lunarx-home")).resolve()
sys.path.insert(0,str(APP_ROOT))
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform
from lunarx_core.catalog import normalize_architecture
from lunarx_core.broker import handle as direct_broker
from lunarx_core.quota import LogicalQuotaProvider, directory_size as logical_directory_size, select_provider

PATHS=resolve_paths(APP_ROOT)
DATA_ROOT=PATHS.data_root; CONFIG_ROOT=PATHS.config_root; CATALOG_PATH=PATHS.catalog_root/"apps.json"
STATE_PATH=CONFIG_ROOT/"state.json"; QUOTAS_PATH=CONFIG_ROOT/"quotas.json"; MAX_INPUT=2*1024*1024
USERNAME_RE=re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
RESERVED={"root","daemon","bin","sys","sync","games","man","lp","mail","news","uucp","proxy","www-data","backup","list","irc","_apt","nobody","lunarx-home"}
STATUS_UNITS={"lunarx-home.service","xrdp.service","xrdp-sesman.service","docker.service","lunarx-storage-scan.timer","lunarx-desktop-governor.timer","lunarx-status-page.service"}
PERMISSION_KEYS={"desktop_access","desktop_app_install","shared_access","drive","photos","files"}

def fail(message:str,code:str="BROKER_REJECTED",detail:str|None=None)->None:
    value={"ok":False,"error":message,"error_code":code}
    if detail:value["detail"]=str(detail)[:240]
    print(json.dumps(value,separators=(",",":"))); raise SystemExit(2)

def command(args:list[str], input_data:bytes|None=None, timeout:int=20)->subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args,input=input_data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=timeout,env={"PATH":"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C.UTF-8"})
    except (OSError,subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args,127,b"",str(exc).encode("utf-8","replace"))

def atomic_json(path:Path,value:dict[str,Any])->None:
    path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,prefix=f".{path.name}-",delete=False) as h:
        temp=Path(h.name); json.dump(value,h,ensure_ascii=False,indent=2,sort_keys=True); h.write("\n")
    os.chmod(temp,0o640)
    try: os.chown(temp,0,grp.getgrnam("lunarx-home").gr_gid)
    except KeyError: pass
    os.replace(temp,path)

def default_state()->dict[str,Any]:
    perms={k:True for k in PERMISSION_KEYS}
    return {"schema_version":"3.1.2","settings":{"shared_contribution_gib":15,"default_quota_gib":64,"desktop_idle_timeout_minutes":10,"theme_mode":"dark","server_name":"LunarX Home"},"users":{},"server_apps":{},"installed_apps":{},"managed_apps":{},"storage":{"members":[],"last_loopback_test":None}}

def load_state()->dict[str,Any]:
    state=default_state()
    try:
        incoming=json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for key in ("settings","users","server_apps","installed_apps","managed_apps","storage"):
            if isinstance(incoming.get(key),dict): state[key].update(incoming[key])
    except (OSError,ValueError,AttributeError): pass
    return state

def save_state(state:dict[str,Any])->None: atomic_json(STATE_PATH,state)

def valid_username(value:object)->str:
    username=str(value or "")
    if not USERNAME_RE.fullmatch(username) or username in RESERVED: fail("invalid username")
    return username

def valid_password(value:object)->str:
    password=str(value or "")
    if len(password)<8 or len(password)>512 or any(x in password for x in ("\r","\n","\0")): fail("invalid password")
    return password

def authenticate(payload:dict[str,Any])->None:
    username=str(payload.get("username") or ""); password=str(payload.get("password") or "")
    if not USERNAME_RE.fullmatch(username) or not password or len(password)>512:
        print('{"ok":true,"authenticated":false}'); return
    pam=ctypes.CDLL(ctypes.util.find_library("pam") or "libpam.so.0"); libc=ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
    class Msg(ctypes.Structure): _fields_=[("style",ctypes.c_int),("msg",ctypes.c_char_p)]
    class Resp(ctypes.Structure): _fields_=[("resp",ctypes.c_char_p),("code",ctypes.c_int)]
    CB=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int,ctypes.POINTER(ctypes.POINTER(Msg)),ctypes.POINTER(ctypes.POINTER(Resp)),ctypes.c_void_p)
    class Conv(ctypes.Structure): _fields_=[("cb",CB),("data",ctypes.c_void_p)]
    libc.calloc.argtypes=[ctypes.c_size_t,ctypes.c_size_t]; libc.calloc.restype=ctypes.c_void_p; libc.strdup.argtypes=[ctypes.c_char_p]; libc.strdup.restype=ctypes.c_void_p
    ub=username.encode(); pb=password.encode()
    @CB
    def callback(count,messages,responses,_data):
        memory=libc.calloc(count,ctypes.sizeof(Resp)); output=ctypes.cast(memory,ctypes.POINTER(Resp))
        for i in range(count):
            value=pb if messages[i].contents.style==1 else ub if messages[i].contents.style==2 else b""
            output[i].resp=ctypes.cast(libc.strdup(value),ctypes.c_char_p); output[i].code=0
        responses[0]=output; return 0
    handle=ctypes.c_void_p(); conv=Conv(callback,None); result=pam.pam_start(b"login",ub,ctypes.byref(conv),ctypes.byref(handle))
    if result==0: result=pam.pam_authenticate(handle,0)
    if result==0: result=pam.pam_acct_mgmt(handle,0)
    if handle: pam.pam_end(handle,result)
    print(json.dumps({"ok":True,"authenticated":result==0},separators=(",",":")))

def human_users()->list[pwd.struct_passwd]:
    return [r for r in pwd.getpwall() if 1000<=r.pw_uid<65534 and r.pw_name!="lunarx-home" and r.pw_shell not in {"/usr/sbin/nologin","/bin/false"}]

def is_enabled(username:str)->bool:
    p=command(["/usr/bin/passwd","-S",username]); fields=p.stdout.decode("utf-8","replace").split()
    return p.returncode==0 and len(fields)>1 and fields[1] not in {"L","LK"}

def append_line(path:Path,line:str)->None:
    existing=path.read_text(encoding="utf-8") if path.exists() else ""
    if line not in existing.splitlines():
        with path.open("a",encoding="utf-8") as h: h.write(line+"\n")

def project_id(name:str,target:Path)->int:
    projid,projects=Path("/etc/projid"),Path("/etc/projects")
    for line in projid.read_text(encoding="utf-8").splitlines() if projid.exists() else []:
        key,sep,value=line.partition(":")
        if sep and key==name: return int(value)
    used=set()
    for line in projects.read_text(encoding="utf-8").splitlines() if projects.exists() else []:
        try: used.add(int(line.partition(":")[0]))
        except ValueError: pass
    candidate=max(3000,max(used,default=2999)+1); append_line(projects,f"{candidate}:{target}"); append_line(projid,f"{name}:{candidate}"); return candidate

def load_quotas()->list[dict[str,Any]]:
    try: return [x for x in json.loads(QUOTAS_PATH.read_text(encoding="utf-8")).get("quotas",[]) if isinstance(x,dict)]
    except (OSError,ValueError,AttributeError): return []

def save_quota(project:str,username:str,hard_gib:int,remove:bool=False)->None:
    quotas=[x for x in load_quotas() if x.get("project")!=project and x.get("username")!=username]
    if not remove: quotas.append({"project":project,"username":username,"hard":f"{hard_gib}G"})
    atomic_json(QUOTAS_PATH,{"quotas":sorted(quotas,key=lambda x:str(x.get("username","")))})

def quota_gib(username:str)->int:
    for item in load_quotas():
        if item.get("username")==username:
            m=re.fullmatch(r"(\d+)G",str(item.get("hard",""))); return int(m.group(1)) if m else 0
    return 0

def directory_bytes(path:Path)->int:
    total=0
    for root,dirs,files in os.walk(path,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(root)/d).is_symlink()]
        for name in files:
            try:
                item=Path(root)/name
                if not item.is_symlink(): total+=item.stat().st_size
            except OSError: pass
    return total

def pool_capacity_gib()->int:
    st=os.statvfs(DATA_ROOT); return int(st.f_blocks*st.f_frsize//1024**3)

def set_quota(username:str,hard_gib:int)->None:
    if hard_gib<15 or hard_gib>100000: fail("quota must be at least 15 GiB")
    minimum=max(15,(directory_bytes(DATA_ROOT/"users"/username)+5*1024**3+1024**3-1)//1024**3)
    if hard_gib<minimum: fail(f"quota cannot be below {minimum} GiB")
    other=sum(quota_gib(r.pw_name) for r in human_users() if r.pw_name!=username)
    if other+quota_gib("shared")+quota_gib("system")+hard_gib>pool_capacity_gib(): fail("quota would overcommit managed capacity")
    project="lx_"+username; target=DATA_ROOT/"users"/username
    info=detect_platform(paths=PATHS)
    if info.quota_mode == "native-filesystem":
        project_id(project,target)
        if command(["/usr/sbin/xfs_quota","-x","-c",f"project -s {project}",str(DATA_ROOT)]).returncode: fail("storage project could not be initialized")
        if command(["/usr/sbin/xfs_quota","-x","-c",f"limit -p bsoft={hard_gib}g bhard={hard_gib}g {project}",str(DATA_ROOT)]).returncode: fail("storage quota could not be applied")
    save_quota(project,username,hard_gib)

def recompute_shared_quota(state:dict[str,Any])->int:
    contribution=int(state["settings"].get("shared_contribution_gib",15)); active=[]
    for username,profile in state.get("users",{}).items():
        try: record=pwd.getpwnam(username)
        except KeyError: continue
        if 1000<=record.pw_uid<65534 and bool(profile.get("enabled",True)) and is_enabled(username): active.append(record)
    hard=contribution*len(active)
    minimum=max(1,(directory_bytes(DATA_ROOT/"shared")+1024**3-1)//1024**3)
    if hard<minimum: fail(f"shared quota cannot be below current usage ({minimum} GiB)")
    if sum(quota_gib(r.pw_name) for r in active)+quota_gib("system")+hard>pool_capacity_gib(): fail("shared contribution would overcommit managed capacity")
    if detect_platform(paths=PATHS).quota_mode == "native-filesystem" and command(["/usr/sbin/xfs_quota","-x","-c",f"limit -p bsoft={hard}g bhard={hard}g lunarx_shared",str(DATA_ROOT)]).returncode: fail("shared quota could not be applied")
    save_quota("lunarx_shared","shared",hard); return hard

def normalized_permissions(value:object,previous:dict[str,Any]|None=None)->dict[str,bool]:
    source=value if isinstance(value,dict) else {}; base={k:True for k in PERMISSION_KEYS}
    if previous: base.update({k:bool(v) for k,v in previous.items() if k in PERMISSION_KEYS})
    base.update({k:bool(v) for k,v in source.items() if k in PERMISSION_KEYS}); return base

def ensure_permissions(username:str,permissions:dict[str,bool])->None:
    command(["/usr/sbin/usermod","-aG","lunarx-shared",username] if permissions.get("shared_access") else ["/usr/bin/gpasswd","-d",username,"lunarx-shared"])
    installer=DATA_ROOT/"users"/username/".appdata/applications/lunarx-app-installer.desktop"
    if permissions.get("desktop_app_install"):
        installer.parent.mkdir(parents=True,exist_ok=True); installer.write_text("[Desktop Entry]\nType=Application\nName=LunarX Applications\nExec=/usr/bin/xdg-open http://lunarx.local/\nIcon=system-software-install\nTerminal=false\nCategories=Utility;\n",encoding="utf-8")
        r=pwd.getpwnam(username)
        for p in (installer.parent.parent.parent,installer.parent.parent,installer.parent,installer):
            try: os.chown(p,r.pw_uid,r.pw_gid)
            except OSError: pass
    else: installer.unlink(missing_ok=True)

def ensure_desktop_defaults(username:str)->None:
    record=pwd.getpwnam(username); home=Path("/home")/username
    autostart=home/".config/autostart"; autostart.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name in ("xfce4-notes-autostart.desktop","xiccd.desktop","xfce4-power-manager.desktop","light-locker.desktop","xdg-user-dirs-kde.desktop"):
        target=autostart/name
        if not target.exists(): target.write_text("[Desktop Entry]\nHidden=true\n",encoding="utf-8")
    thunar=home/".config/Thunar"; thunar.mkdir(mode=0o700,parents=True,exist_ok=True); thunarrc=thunar/"thunarrc"
    if not thunarrc.exists(): thunarrc.write_text("[Configuration]\nMiscThumbnailMode=THUNAR_THUMBNAIL_MODE_NEVER\n",encoding="utf-8")
    xfwm=home/".config/xfce4/xfconf/xfce-perchannel-xml"; xfwm.mkdir(mode=0o700,parents=True,exist_ok=True); xfwmrc=xfwm/"xfwm4.xml"
    if not xfwmrc.exists(): xfwmrc.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<channel name="xfwm4" version="1.0"><property name="general" type="empty"><property name="use_compositing" type="bool" value="false"/></property></channel>\n',encoding="utf-8")
    environment_dir=home/".config/environment.d"; environment_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    appdata=DATA_ROOT/"users"/username/".appdata"
    environment=(
        f"XDG_DATA_HOME={appdata}\n"
        f"XDG_CACHE_HOME={appdata}/cache\n"
        f"XDG_DATA_DIRS={appdata}/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share\n"
    )
    (environment_dir/"90-lunarx-apps.conf").write_text(environment,encoding="utf-8")
    (home/".xsessionrc").write_text(
        f"export XDG_DATA_HOME='{appdata}'\n"
        f"export XDG_CACHE_HOME='{appdata}/cache'\n"
        f"export XDG_DATA_DIRS='{appdata}/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share'\n",
        encoding="utf-8",
    )
    for root,dirs,files in os.walk(home/".config",followlinks=False):
        for name in dirs+files:
            path=Path(root)/name
            if path.is_symlink(): continue
            try: os.chown(path,record.pw_uid,record.pw_gid)
            except OSError: pass

def ensure_storage(username:str,hard:int,permissions:dict[str,bool])->None:
    r=pwd.getpwnam(username); home=Path("/home")/username; home.mkdir(mode=0o700,parents=False,exist_ok=True); os.chown(home,r.pw_uid,r.pw_gid); os.chmod(home,0o700)
    base=DATA_ROOT/"users"/username
    for child in ("Documentos","Downloads","Fotos","Videos","Musica","Arquivados",".appdata"): (base/child).mkdir(mode=0o700,parents=True,exist_ok=True)
    for item in (base,*base.iterdir()): os.chown(item,r.pw_uid,r.pw_gid); os.chmod(item,0o700)
    workspace=base/"Workspace"; workspace.mkdir(mode=0o700,parents=True,exist_ok=True); os.chown(workspace,r.pw_uid,r.pw_gid); os.chmod(workspace,0o700)
    set_quota(username,hard); ensure_permissions(username,permissions); ensure_desktop_defaults(username)
    if Path("/usr/bin/setfacl").exists(): command(["/usr/bin/setfacl","-m","u:lunarx-home:rwx",str(base)]); command(["/usr/bin/setfacl","-m","d:u:lunarx-home:rwx",str(base)]); command(["/usr/bin/setfacl","-m","u:lunarx-home:rwx",str(workspace)]); command(["/usr/bin/setfacl","-m","d:u:lunarx-home:rwx",str(workspace)])
    session=home/".xsession"; session.write_text("[ -r \"$HOME/.xsessionrc\" ] && . \"$HOME/.xsessionrc\"\nexec startxfce4\n",encoding="utf-8")
    for path in (session,home/".xsessionrc"):
        os.chown(path,r.pw_uid,r.pw_gid); os.chmod(path,0o600)

def provision_existing_user(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); record=pwd.getpwnam(username)
    if not 1000<=record.pw_uid<65534: fail("system account rejected")
    state=load_state(); profile=state["users"].setdefault(username,{})
    permissions=normalized_permissions(payload.get("permissions"),profile.get("permissions"))
    quota=int(payload.get("quota_gib",state["settings"].get("default_quota_gib",64)))
    ensure_storage(username,quota,permissions)
    profile.update({"display_name":str(payload.get("display_name") or profile.get("display_name") or username.title())[:80],"avatar":str(profile.get("avatar") or "")[:1048576],"theme":str(profile.get("theme") or "dark"),"enabled":True,"is_admin":True,"role":"admin","auth_provider":"pam","permissions":permissions})
    save_state(state); recompute_shared_quota(state)
    print(json.dumps({"ok":True,"username":username,"quota_gib":quota},separators=(",",":")))

def storage_target(username:str,scope:str,relative:object,allow_root:bool=False,allow_upload_temp:bool=False,allow_hidden:bool=False)->tuple[Path,Path]:
    username=valid_username(username)
    try: record=pwd.getpwnam(username)
    except KeyError: fail("user not found")
    if not 1000<=record.pw_uid<65534: fail("system account rejected")
    if scope not in {"personal","shared","workspace","desktop"}: fail("invalid storage scope")
    value=str(relative or "")
    if "\\" in value or value.startswith("/") or "\0" in value: fail("invalid storage path")
    normalized=posixpath.normpath(value) if value else "."
    parts=[part for part in PurePosixPath(normalized).parts if part not in {"","."}]
    if normalized==".." or normalized.startswith("../") or any(part in {".",".."} for part in parts): fail("invalid storage path")
    for part in parts:
        if part.startswith(".") and not (allow_hidden or (allow_upload_temp and part.startswith(".lunarx-upload-"))): fail("hidden storage path rejected")
    if scope=="personal": base=(DATA_ROOT/"users"/username).resolve(strict=False)
    elif scope=="shared": base=(DATA_ROOT/"shared").resolve(strict=False)
    elif scope=="workspace": base=(DATA_ROOT/"users"/username/"Workspace").resolve(strict=False)
    else: base=PATHS.desktop_path(username).resolve(strict=False)
    target=base.joinpath(*parts)
    cursor=base
    for part in parts:
        cursor=cursor/part
        if cursor.is_symlink(): fail("storage symlink rejected")
    resolved=target.resolve(strict=False)
    try: resolved.relative_to(base)
    except ValueError: fail("storage path escaped root")
    if target==base and not allow_root: fail("storage root rejected")
    return base,target

def apply_storage_acl(path:Path,scope:str,record:pwd.struct_passwd)->None:
    if path.is_symlink(): fail("storage symlink rejected")
    acl_scope="shared" if scope=="shared" else "personal"
    shared_gid=grp.getgrnam("lunarx-shared").gr_gid if acl_scope=="shared" else record.pw_gid
    os.chown(path,record.pw_uid,shared_gid,follow_symlinks=False)
    if path.is_dir():
        os.chmod(path,0o2770 if acl_scope=="shared" else 0o700,follow_symlinks=False)
        acl=("u::rwx,u:lunarx-home:rwx,g::rwx,m::rwx,o::---" if acl_scope=="shared" else "u::rwx,u:lunarx-home:rwx,g::---,m::rwx,o::---")
        default=("d:u::rwx,d:u:lunarx-home:rwx,d:g::rwx,d:m::rwx,d:o::---" if acl_scope=="shared" else "d:u::rwx,d:u:lunarx-home:rwx,d:g::---,d:m::rwx,d:o::---")
        if command(["/usr/bin/setfacl","-m",acl,"-m",default,str(path)]).returncode: fail("storage directory ACL failed")
    elif path.is_file():
        os.chmod(path,0o660 if acl_scope=="shared" else 0o600,follow_symlinks=False)
        acl=("u::rw-,u:lunarx-home:rw-,g::rw-,m::rw-,o::---" if acl_scope=="shared" else "u::rw-,u:lunarx-home:rw-,g::---,m::rw-,o::---")
        if command(["/usr/bin/setfacl","-m",acl,str(path)]).returncode: fail("storage file ACL failed")
    else: fail("unsupported storage item")

def normalize_storage_item(path:Path,scope:str,record:pwd.struct_passwd,recursive:bool)->int:
    if not path.exists() or path.is_symlink(): fail("storage item unavailable")
    changed=0
    if recursive and path.is_dir():
        for root,dirs,files in os.walk(path,topdown=True,followlinks=False):
            root_path=Path(root)
            if any((root_path/name).is_symlink() for name in dirs+files): fail("storage tree contains symlink")
            apply_storage_acl(root_path,scope,record); changed+=1
            for name in files: apply_storage_acl(root_path/name,scope,record); changed+=1
    else: apply_storage_acl(path,scope,record); changed=1
    return changed

def normalize_storage(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); scope=str(payload.get("scope") or "personal")
    record=pwd.getpwnam(username); paths=payload.get("paths")
    if not isinstance(paths,list) or not paths or len(paths)>250: fail("invalid storage path list")
    recursive=bool(payload.get("recursive",False)); changed=0; normalized=[]
    for value in paths:
        _,target=storage_target(username,scope,value,allow_upload_temp=bool(payload.get("allow_upload_temp",False)),allow_hidden=bool(payload.get("allow_hidden",False)))
        changed+=normalize_storage_item(target,scope,record,recursive); normalized.append(str(value))
    print(json.dumps({"ok":True,"normalized":normalized,"items_changed":changed},ensure_ascii=False,separators=(",",":")))

def prepare_storage_directory(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); scope=str(payload.get("scope") or "personal")
    record=pwd.getpwnam(username); base,target=storage_target(username,scope,payload.get("path"),allow_root=True,allow_hidden=bool(payload.get("allow_hidden",False)))
    if target.exists() and (not target.is_dir() or target.is_symlink()): fail("storage destination is not a directory")
    cursor=base
    created=[]
    for part in target.relative_to(base).parts:
        cursor=cursor/part
        if cursor.is_symlink(): fail("storage symlink rejected")
        if not cursor.exists(): cursor.mkdir(mode=0o2770 if scope=="shared" else 0o700); created.append(cursor)
        if not cursor.is_dir(): fail("storage destination is not a directory")
        apply_storage_acl(cursor,scope,record)
    print(json.dumps({"ok":True,"path":str(payload.get("path") or ""),"created":len(created)},separators=(",",":")))

def repair_user_storage(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); record=pwd.getpwnam(username)
    _,target=storage_target(username,"personal","",allow_root=True)
    changed=normalize_storage_item(target,"personal",record,True)
    print(json.dumps({"ok":True,"username":username,"items_changed":changed},separators=(",",":")))

def optimize_desktop(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); ensure_desktop_defaults(username)
    record=pwd.getpwnam(username); environment={"PATH":"/usr/bin:/bin","HOME":record.pw_dir,"USER":username,"LOGNAME":username,"DBUS_SESSION_BUS_ADDRESS":f"unix:path=/run/user/{record.pw_uid}/bus"}
    result=subprocess.run(["/usr/bin/xfconf-query","-c","xfwm4","-p","/general/use_compositing","-s","false"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=10,env=environment,preexec_fn=lambda:(os.setgid(record.pw_gid),os.setuid(record.pw_uid)))
    print(json.dumps({"ok":True,"username":username,"live_compositor_update":result.returncode==0},separators=(",",":")))

def _desktop_state(username:str)->tuple[int,int,Path]:
    record=pwd.getpwnam(username); display=20+(record.pw_uid%40); return display,5900+display,PATHS.state_root/"desktop"/f"{username}.json"

def desktop_session(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); info=detect_platform(paths=PATHS)
    if info.desktop_backend!="tigervnc-novnc":fail("TigerVNC + noVNC desktop provider is unavailable","DESKTOP_PROVIDER_UNAVAILABLE")
    vncserver=shutil.which("tigervncserver") or shutil.which("vncserver"); vncpasswd=shutil.which("vncpasswd")
    if not vncserver or not vncpasswd:fail("TigerVNC is not installed","DESKTOP_PROVIDER_UNAVAILABLE")
    record=pwd.getpwnam(username); home=Path(record.pw_dir); home.mkdir(mode=0o700,parents=True,exist_ok=True); os.chown(home,record.pw_uid,record.pw_gid)
    display,port,state_path=_desktop_state(username); state_path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    secret=""
    try:
        current=json.loads(state_path.read_text(encoding="utf-8")); secret=str(current.get("password") or "")
    except (OSError,ValueError,TypeError):pass
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}",secret):secret=__import__("secrets").token_urlsafe(9)[:12]
    vnc_dir=home/".vnc"; vnc_dir.mkdir(mode=0o700,parents=True,exist_ok=True); os.chown(vnc_dir,record.pw_uid,record.pw_gid)
    encoded=command([vncpasswd,"-f"],(secret+"\n").encode(),10)
    if encoded.returncode or not encoded.stdout:fail("VNC credential setup failed","DESKTOP_PROVIDER_ERROR",encoded.stderr.decode("utf-8","replace"))
    passwd_file=vnc_dir/"passwd"; passwd_file.write_bytes(encoded.stdout); os.chown(passwd_file,record.pw_uid,record.pw_gid); os.chmod(passwd_file,0o600)
    xstartup=vnc_dir/"xstartup"; xstartup.write_text("#!/bin/sh\nunset SESSION_MANAGER\nunset DBUS_SESSION_BUS_ADDRESS\nexec startxfce4\n",encoding="utf-8"); os.chown(xstartup,record.pw_uid,record.pw_gid); os.chmod(xstartup,0o700)
    environment=[f"HOME={home}",f"USER={username}",f"LOGNAME={username}","PATH=/usr/local/bin:/usr/bin:/bin"]
    probe=as_user(record,environment,[vncserver,"-list"],20); running=f":{display}" in probe.stdout.decode("utf-8","replace")
    if not running:
        started=as_user(record,environment,[vncserver,f":{display}","-localhost","yes","-geometry","1280x720","-depth","24","-SecurityTypes","VncAuth"],60)
        if started.returncode:fail("graphical desktop could not be started","DESKTOP_START_FAILED",(started.stderr or started.stdout).decode("utf-8","replace"))
    atomic_json(state_path,{"username":username,"display":display,"port":port,"password":secret,"provider":"tigervnc-localhost","updated":int(time.time())}); os.chmod(state_path,0o600)
    print(json.dumps({"ok":True,"username":username,"provider":"tigervnc-localhost","display":display,"port":port,"password":secret},separators=(",",":")))

def terminate_desktop(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); terminated=[]
    try:
        record=pwd.getpwnam(username); display,_port,state_path=_desktop_state(username); vncserver=shutil.which("tigervncserver") or shutil.which("vncserver")
        if state_path.exists() and vncserver:
            environment=[f"HOME={record.pw_dir}",f"USER={username}",f"LOGNAME={username}","PATH=/usr/local/bin:/usr/bin:/bin"]
            if as_user(record,environment,[vncserver,"-kill",f":{display}"],20).returncode==0:terminated.append(str(display))
            state_path.unlink(missing_ok=True)
    except (KeyError,OSError):pass
    if Path("/run/systemd/system").exists() and shutil.which("loginctl"):
        sessions=command([shutil.which("loginctl") or "/usr/bin/loginctl","list-sessions","--no-legend"],timeout=20).stdout.decode("utf-8","replace").splitlines()
        for line in sessions:
            fields=line.split()
            if len(fields)<2 or fields[1]!=username:continue
            sid=fields[0]; kind=command([shutil.which("loginctl") or "/usr/bin/loginctl","show-session",sid,"-p","Type","--value"],timeout=10).stdout.decode().strip(); cls=command([shutil.which("loginctl") or "/usr/bin/loginctl","show-session",sid,"-p","Class","--value"],timeout=10).stdout.decode().strip()
            if cls=="user" and kind in {"x11","wayland"} and command([shutil.which("loginctl") or "/usr/bin/loginctl","terminate-session",sid],timeout=20).returncode==0:terminated.append(sid)
    print(json.dumps({"ok":True,"username":username,"terminated_sessions":terminated},separators=(",",":")))

def create_user(payload:dict[str,Any])->None:
    username,password=valid_username(payload.get("username")),valid_password(payload.get("password"))
    try: pwd.getpwnam(username); fail("user already exists")
    except KeyError: pass
    state=load_state(); quota=int(payload.get("quota_gib",state["settings"].get("default_quota_gib",64))); permissions=normalized_permissions(payload.get("permissions"))
    if command(["/usr/sbin/useradd","--no-create-home","--shell","/bin/bash",username]).returncode: fail("user could not be created")
    try:
        if command(["/usr/sbin/chpasswd"],f"{username}:{password}\n".encode()).returncode: fail("password could not be set")
        ensure_storage(username,quota,permissions); state["users"][username]={"display_name":str(payload.get("display_name") or username.title())[:80],"avatar":str(payload.get("avatar") or "")[:1048576],"theme":"dark","enabled":True,"permissions":permissions}; save_state(state); recompute_shared_quota(state)
    except BaseException:
        command(["/usr/sbin/userdel","--remove",username]); shutil.rmtree(DATA_ROOT/"users"/username,ignore_errors=True); raise
    print(json.dumps({"ok":True,"username":username},separators=(",",":")))

def rename_user(old:str,new_value:str,state:dict[str,Any])->str:
    new=valid_username(new_value)
    if old==new:return old
    try: pwd.getpwnam(new); fail("new username already exists")
    except KeyError: pass
    old_base,new_base=DATA_ROOT/"users"/old,DATA_ROOT/"users"/new; quota=quota_gib(old)
    # Canonical rename requires ending the user's active graphical/login processes.
    command(["/usr/bin/loginctl","terminate-user",old]); command(["/usr/bin/pkill","-TERM","-u",old]); time.sleep(1)
    if command(["/usr/sbin/usermod","-l",new,"-d",f"/home/{new}","-m",old]).returncode: fail("Linux account rename failed")
    if command(["/usr/sbin/groupmod","-n",new,old]).returncode: command(["/usr/sbin/usermod","-l",old,"-d",f"/home/{old}","-m",new]); fail("primary group rename failed")
    try:
        if old_base.exists(): old_base.rename(new_base)
        projects=Path("/etc/projects"); projid=Path("/etc/projid")
        if projects.exists(): projects.write_text(projects.read_text().replace(f":{old_base}\n",f":{new_base}\n"),encoding="utf-8")
        if projid.exists(): projid.write_text(projid.read_text().replace(f"lx_{old}:",f"lx_{new}:"),encoding="utf-8")
        save_quota("lx_"+old,old,0,True); save_quota("lx_"+new,new,quota); state["users"][new]=state["users"].pop(old,{}); set_quota(new,quota)
    except Exception:
        if new_base.exists() and not old_base.exists(): new_base.rename(old_base)
        command(["/usr/sbin/groupmod","-n",old,new]); command(["/usr/sbin/usermod","-l",old,"-d",f"/home/{old}","-m",new]); fail("rename rolled back")
    return new

def update_user(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username"))
    try: r=pwd.getpwnam(username)
    except KeyError: fail("user not found")
    if not 1000<=r.pw_uid<65534: fail("system account rejected")
    state=load_state(); profile=state["users"].setdefault(username,{})
    if payload.get("new_username"): username=rename_user(username,str(payload["new_username"]),state); profile=state["users"].setdefault(username,profile)
    if payload.get("password") is not None:
        password=valid_password(payload["password"])
        if command(["/usr/sbin/chpasswd"],f"{username}:{password}\n".encode()).returncode: fail("password change failed")
    if "display_name" in payload: profile["display_name"]=str(payload["display_name"])[:80]
    if "avatar" in payload:
        avatar=str(payload["avatar"]); profile["avatar"]=avatar if len(avatar)<=1048576 and (not avatar or avatar.startswith("data:image/")) else ""
    if "theme" in payload: profile["theme"]="light" if str(payload.get("theme"))=="light" else "dark"
    permissions=normalized_permissions(payload.get("permissions"),profile.get("permissions")); profile["permissions"]=permissions
    if "enabled" in payload:
        enabled=bool(payload["enabled"])
        if enabled != is_enabled(username) and command(["/usr/sbin/usermod","-U" if enabled else "-L",username]).returncode: fail("account state change failed")
        profile["enabled"]=enabled
    if "quota_gib" in payload: set_quota(username,int(payload["quota_gib"]))
    ensure_permissions(username,permissions); save_state(state); recompute_shared_quota(state); print(json.dumps({"ok":True,"username":username},separators=(",",":")))

def delete_user(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username"))
    try:r=pwd.getpwnam(username)
    except KeyError:fail("user not found")
    if not 1000<=r.pw_uid<65534:fail("system account rejected")
    project="lx_"+username
    # Clear the XFS project limits while the name-to-ID mapping still exists.
    if command(["/usr/sbin/xfs_quota","-x","-c",f"limit -p bsoft=0 bhard=0 isoft=0 ihard=0 {project}",str(DATA_ROOT)]).returncode:
        fail("user quota could not be cleared")
    if command(["/usr/sbin/userdel","--remove",username]).returncode:fail("user could not be deleted")
    shutil.rmtree(DATA_ROOT/"users"/username,ignore_errors=True)
    for path,matcher in [(Path("/etc/projects"),lambda line:line.partition(":")[2]!=str(DATA_ROOT/"users"/username)),(Path("/etc/projid"),lambda line:line.partition(":")[0]!=project)]:
        if path.exists():path.write_text("\n".join(x for x in path.read_text().splitlines() if matcher(x))+"\n",encoding="utf-8")
    save_quota(project,username,0,True); state=load_state(); state["users"].pop(username,None); save_state(state); recompute_shared_quota(state); print(json.dumps({"ok":True,"username":username},separators=(",",":")))

def list_users()->None:
    state=load_state(); users=[]
    for username in sorted(state.get("users",{})):
        try: r=pwd.getpwnam(username)
        except KeyError: continue
        if not 1000<=r.pw_uid<65534: continue
        profile=state["users"].get(r.pw_name,{})
        users.append({"username":r.pw_name,"display_name":profile.get("display_name",r.pw_name.title()),"avatar":profile.get("avatar",""),"enabled":is_enabled(r.pw_name),"quota_gib":quota_gib(r.pw_name),"used_bytes":directory_bytes(DATA_ROOT/"users"/r.pw_name),"permissions":normalized_permissions({},profile.get("permissions"))})
    print(json.dumps({"ok":True,"users":users},ensure_ascii=False,separators=(",",":")))

def settings_update(payload:dict[str,Any])->None:
    state=load_state(); hard=quota_gib("shared")
    if "shared_contribution_gib" in payload:
        value=int(payload["shared_contribution_gib"])
        if value<1 or value>10000:fail("invalid shared contribution")
        old=state["settings"].get("shared_contribution_gib",15); state["settings"]["shared_contribution_gib"]=value
        try:hard=recompute_shared_quota(state)
        except BaseException:state["settings"]["shared_contribution_gib"]=old;raise
    if "default_quota_gib" in payload:
        value=int(payload["default_quota_gib"])
        if value<15:fail("default quota must be at least 15 GiB")
        state["settings"]["default_quota_gib"]=value
    if "desktop_idle_timeout_minutes" in payload:
        value=int(payload["desktop_idle_timeout_minutes"])
        if value<5 or value>240:fail("desktop idle timeout must be between 5 and 240 minutes")
        state["settings"]["desktop_idle_timeout_minutes"]=value
    if "theme_mode" in payload:
        theme=str(payload["theme_mode"])
        if theme not in {"dark","light","system"}:fail("invalid theme mode")
        state["settings"]["theme_mode"]=theme
    if "server_name" in payload:
        name=str(payload["server_name"]).strip()[:80]
        if not name:fail("server name cannot be empty")
        state["settings"]["server_name"]=name
    save_state(state); print(json.dumps({"ok":True,"settings":state["settings"],"shared_quota_gib":hard},separators=(",",":")))

def service_action(payload:dict[str,Any])->None:
    units=payload.get("units") if isinstance(payload.get("units"),list) else [payload.get("unit")]
    if not units or any(str(x) not in STATUS_UNITS for x in units):fail("invalid service request")
    op=str(payload.get("operation","status"))
    if op not in {"status","restart"}:fail("invalid service operation")
    if detect_platform(paths=PATHS).service_manager != "systemd":
        print(json.dumps({"ok":False,"error":"service manager unavailable on this platform","error_code":"service_manager_unavailable","service_manager":detect_platform(paths=PATHS).service_manager},separators=(",",":"))); return
    output={}
    for unit in units:
        if op=="restart" and command(["/usr/bin/systemctl","restart",str(unit)],timeout=40).returncode:fail("service restart failed")
        output[str(unit)]=command(["/usr/bin/systemctl","is-active",str(unit)]).stdout.decode().strip() or "unknown"
    print(json.dumps({"ok":True,"units":output},separators=(",",":")))

def storage_status()->None:
    info=detect_platform(paths=PATHS)
    try:
        usage=os.statvfs(DATA_ROOT); total=usage.f_blocks*usage.f_frsize; free=usage.f_bavail*usage.f_frsize
    except OSError:
        total=free=0
    mount=command(["/usr/bin/findmnt","-rn","-M",str(DATA_ROOT),"-o","SOURCE,FSTYPE,OPTIONS,SIZE,USED,AVAIL"],timeout=10)
    disks=command(["/usr/bin/lsblk","-J","-b","-o","NAME,PATH,SIZE,TYPE,FSTYPE,FSAVAIL,FSUSE%,LABEL,UUID,MOUNTPOINTS,MODEL,TRAN,RM,ROTA"],timeout=20)
    try: disk_data=json.loads(disks.stdout.decode())
    except (ValueError,UnicodeDecodeError): disk_data={"blockdevices":[]}
    quota_report="not-applicable"
    if info.quota_mode=="native-filesystem" and shutil.which("xfs_quota"):
        quota=command([shutil.which("xfs_quota") or "/usr/sbin/xfs_quota","-x","-c","report -p -b -N",str(DATA_ROOT)],timeout=20)
        quota_report=quota.stdout.decode("utf-8","replace").strip() if quota.returncode==0 else "native quota report unavailable"
    state=load_state(); members=[]
    for item in state["storage"].get("members",[]):
        if not isinstance(item,dict): continue
        member=dict(item); mount_path=Path(str(member.get("mount", ""))); member["online"]=mount_path.is_dir() and (mount_path.is_mount() or info.quota_mode!="native-filesystem"); members.append(member)
    print(json.dumps({"ok":True,"provider":"native-filesystem" if info.quota_mode=="native-filesystem" else "logical","platform":info.as_dict(),"mount":mount.stdout.decode().strip(),"quota_report":quota_report,"total_bytes":total,"free_bytes":free,"disks":disk_data.get("blockdevices",[]),"managed_members":members,"last_loopback_test":state["storage"].get("last_loopback_test")},separators=(",",":")))

def stable_disk_source(value:object)->str:
    source=str(value or "").strip()
    if source.startswith("UUID=") and re.fullmatch(r"UUID=[A-Fa-f0-9-]{8,}",source):return source
    if re.fullmatch(r"/dev/disk/by-id/[A-Za-z0-9._:+-]+",source) and Path(source).is_symlink():return source
    fail("disk identity must be an existing filesystem UUID or /dev/disk/by-id path")

def storage_register(payload:dict[str,Any])->None:
    source=stable_disk_source(payload.get("source")); name=str(payload.get("name") or "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,31}",name):fail("invalid additional disk name")
    probe=source[5:] if source.startswith("UUID=") else source
    if source.startswith("UUID="):
        found=command(["/usr/sbin/blkid","-U",probe])
        if found.returncode:fail("filesystem UUID is not currently available")
        device=found.stdout.decode().strip()
    else:device=str(Path(source).resolve())
    root_source=command(["/usr/bin/findmnt","-rn","-o","SOURCE","/"]).stdout.decode().strip()
    if device==root_source or (root_source.startswith("/dev/") and device.startswith(root_source)):fail("system filesystem cannot be registered as an additional disk")
    fstype=command(["/usr/sbin/blkid","-o","value","-s","TYPE",device]).stdout.decode().strip()
    if fstype not in {"xfs","ext4","btrfs"}:fail("additional disk must already contain a supported Linux filesystem")
    mount=Path("/srv/lunarx-disks")/name; mount.mkdir(mode=0o750,parents=True,exist_ok=True)
    marker=f"# lunarx-additional:{name}"; fstab=Path("/etc/fstab"); lines=[line for line in fstab.read_text(encoding="utf-8").splitlines() if marker not in line]
    lines.append(f"{source} {mount} {fstype} nofail,x-systemd.automount,x-systemd.device-timeout=10,nodev,nosuid 0 2 {marker}")
    temporary=fstab.with_name(f".fstab-lunarx-{os.getpid()}"); temporary.write_text("\n".join(lines)+"\n",encoding="utf-8"); os.chmod(temporary,0o644); os.replace(temporary,fstab)
    command(["/usr/bin/systemctl","daemon-reload"]); mounted=command(["/usr/bin/mount",str(mount)],timeout=30)
    state=load_state(); members=[item for item in state["storage"].get("members",[]) if item.get("name")!=name]
    members.append({"name":name,"source":source,"mount":str(mount),"filesystem":fstype,"online":mounted.returncode==0,"grants":[]}); state["storage"]["members"]=members; save_state(state)
    print(json.dumps({"ok":True,"member":members[-1]},separators=(",",":")))

def storage_grant(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); name=str(payload.get("name") or ""); access=str(payload.get("access") or "rw")
    if access not in {"ro","rw","none"}:fail("invalid disk access")
    state=load_state(); member=next((item for item in state["storage"].get("members",[]) if item.get("name")==name),None)
    if not isinstance(member,dict):fail("additional disk is not registered")
    mount=Path(str(member.get("mount","")))
    if not mount.is_mount():fail("additional disk is offline")
    record=pwd.getpwnam(username); perms="r-x" if access=="ro" else "rwx"
    if access=="none":
        command(["/usr/bin/setfacl","-x",f"u:{username}",str(mount)]); command(["/usr/bin/setfacl","-x",f"d:u:{username}",str(mount)])
    else:
        if command(["/usr/bin/setfacl","-m",f"u:{username}:{perms}","-m",f"d:u:{username}:{perms}",str(mount)]).returncode:fail("disk ACL could not be applied")
    grants=[item for item in member.get("grants",[]) if item.get("username")!=username]
    if access!="none":grants.append({"username":username,"access":access})
    member["grants"]=grants; save_state(state)
    print(json.dumps({"ok":True,"name":name,"username":username,"access":access},separators=(",",":")))

def loopback_test()->None:
    image=Path("/var/lib/lunarx-home/storage-member-validation.img"); mount=Path("/mnt/lunarx-member-validation"); mount.mkdir(parents=True,exist_ok=True); image.parent.mkdir(parents=True,exist_ok=True); loop=""; passed=False
    try:
        with image.open("wb") as h:h.truncate(512*1024*1024)
        p=command(["/usr/sbin/losetup","--find","--show",str(image)]); loop=p.stdout.decode().strip()
        if p.returncode or not loop:fail("loopback allocation failed")
        if command(["/usr/sbin/mkfs.xfs","-f","-L","LunarX-Test",loop],timeout=40).returncode:fail("loopback XFS format failed")
        if command(["/usr/bin/mount","-o","prjquota",loop,str(mount)]).returncode:fail("loopback mount failed")
        probe=mount/"member-ok"; probe.write_text("LunarX multi-member validation\n"); passed=probe.read_text().startswith("LunarX")
    finally:
        command(["/usr/bin/umount",str(mount)])
        if loop:command(["/usr/sbin/losetup","-d",loop])
        image.unlink(missing_ok=True)
    state=load_state(); state["storage"]["last_loopback_test"]={"passed":passed,"timestamp":int(time.time()),"filesystem":"xfs","quota_mount_option":"prjquota","physical_disks_touched":False}; save_state(state); print(json.dumps({"ok":passed,"result":state["storage"]["last_loopback_test"]},separators=(",",":")))

def catalog_app(app_id:str)->dict[str,Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,190}",app_id): fail("invalid application id")
    try: document=json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError,ValueError): fail("application catalog unavailable")
    for item in document.get("apps",[]):
        if isinstance(item,dict) and item.get("id")==app_id:return item
    fail("application is not in the verified catalog")


def user_app_env(username:str)->tuple[pwd.struct_passwd,list[str]]:
    username=valid_username(username)
    try:record=pwd.getpwnam(username)
    except KeyError:fail("user not found")
    if not 1000<=record.pw_uid<65534:fail("system account rejected")
    data=DATA_ROOT/"users"/username/".appdata"; cache=data/"cache"; data.mkdir(mode=0o700,parents=True,exist_ok=True); cache.mkdir(mode=0o700,parents=True,exist_ok=True)
    for path in (data,cache):os.chown(path,record.pw_uid,record.pw_gid);os.chmod(path,0o700)
    return record,[f"HOME={record.pw_dir}",f"USER={username}",f"LOGNAME={username}",f"XDG_DATA_HOME={data}",f"XDG_CACHE_HOME={cache}",f"XDG_RUNTIME_DIR=/run/user/{record.pw_uid}","PATH=/usr/local/bin:/usr/bin:/bin"]


def as_user(record:pwd.struct_passwd,environment:list[str],args:list[str],timeout:int=1800)->subprocess.CompletedProcess[bytes]:
    return command(["/usr/sbin/runuser","--user",record.pw_name,"--","/usr/bin/env",*environment,*args],timeout=timeout)


def ensure_flathub(record:pwd.struct_passwd,environment:list[str])->None:
    result=as_user(record,environment,["/usr/bin/flatpak","--user","remote-add","--if-not-exists","flathub","https://dl.flathub.org/repo/flathub.flatpakrepo"],120)
    if result.returncode:fail("Flathub remote could not be configured")


def is_lunarx_admin(username:str)->bool:
    try:record=pwd.getpwnam(username); groups={grp.getgrgid(record.pw_gid).gr_name}
    except KeyError:return False
    groups.update(group.gr_name for group in grp.getgrall() if username in group.gr_mem)
    return "lunarx-admin" in groups


def app_provider(app:dict[str,Any],provider_id:str)->dict[str,Any]:
    providers=app.get("providers",[])
    if isinstance(providers,list):
        for provider in providers:
            if isinstance(provider,dict) and str(provider.get("id"))==provider_id:return provider
    fail("application provider is not allow-listed","APP_PROVIDER_INVALID")


def provider_supported(provider:dict[str,Any])->bool:
    info=detect_platform(paths=PATHS); platforms={str(v) for v in provider.get("platforms",[])}; arches={normalize_architecture(str(v)) for v in provider.get("architectures",[])}
    return ("all" in platforms or info.mode in platforms) and ("all" in arches or normalize_architecture(info.architecture) in arches) and provider.get("provider")!="unsupported"


def package_version(package:str)->str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+",package):return ""
    result=command(["/usr/bin/dpkg-query","-W","-f=${Status}\\t${Version}",package],timeout=10)
    text=result.stdout.decode("utf-8","replace")
    return text.split("\t",1)[1].strip() if result.returncode==0 and text.startswith("install ok installed\t") else ""


def state_user_apps(state:dict[str,Any],username:str)->list[dict[str,Any]]:
    values=state.setdefault("installed_apps",{}).get(username,[])
    return [dict(item) for item in values if isinstance(item,dict) and item.get("id")]


def put_state_user_app(state:dict[str,Any],username:str,record:dict[str,Any]|None,app_id:str)->None:
    values=[item for item in state_user_apps(state,username) if item.get("id")!=app_id]
    if record is not None:values.append(record)
    state.setdefault("installed_apps",{})[username]=values


def user_app_list(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); apps=[]; state=load_state(); record,environment=user_app_env(username)
    if Path("/usr/bin/flatpak").is_file() and not detect_platform(paths=PATHS).is_proot:
        result=as_user(record,environment,["/usr/bin/flatpak","--user","list","--app","--columns=application,name,version"],120)
        updates_result=as_user(record,environment,["/usr/bin/flatpak","--user","remote-ls","--updates","--columns=application","flathub"],120)
        update_ids={row.strip() for row in updates_result.stdout.decode("utf-8","replace").splitlines() if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,190}",row.strip())} if updates_result.returncode==0 else set()
        if result.returncode==0:
            for row in result.stdout.decode("utf-8","replace").splitlines():
                fields=row.split("\t"); apps.append({"id":fields[0],"name":fields[1] if len(fields)>1 else fields[0],"version":fields[2] if len(fields)>2 else "","provider":"flatpak-user","scope":"user","update_available":fields[0] in update_ids})
    apps.extend(state_user_apps(state,username))
    managed=state.get("managed_apps",{})
    if isinstance(managed,dict):
        for app_id,item in managed.items():
            if not isinstance(item,dict) or not item.get("installed"):continue
            packages=[str(v) for v in item.get("packages",[])]; versions=[package_version(pkg) for pkg in packages]
            if packages and any(not version for version in versions):continue
            value=dict(item);value.update({"id":app_id,"version":", ".join(versions) if versions else str(item.get("version") or "system"),"scope":"system"});apps.append(value)
    dedup={str(item.get("id")):item for item in apps if item.get("id")}
    print(json.dumps({"ok":True,"apps":list(dedup.values()),"provider":"platform-aware","providers":sorted({str(v.get("provider","unknown")) for v in dedup.values()})},ensure_ascii=False,separators=(",",":")))


def user_app(payload:dict[str,Any])->None:
    username=valid_username(payload.get("username")); app_id=str(payload.get("app_id","")); operation=str(payload.get("operation","")); app=catalog_app(app_id)
    if operation not in {"install","launch","update","uninstall"}:fail("application operation is not allow-listed")
    provider=app_provider(app,str(payload.get("provider_id","")))
    if not provider_supported(provider):fail(str(provider.get("reason") or "application provider unavailable"),"APP_PROVIDER_UNAVAILABLE")
    if (app.get("admin_only") or provider.get("requires_admin")) and not is_lunarx_admin(username):fail("administrator role required")
    if app.get("installable") is False and operation!="launch":fail("automatic installation is unavailable for this verified-source entry")
    backend=str(provider.get("provider","")); record,environment=user_app_env(username); state=load_state()
    if backend=="web-pwa":
        launch_url=str(provider.get("launch_url","")); now=int(time.time())
        if operation in {"install","update"}:
            put_state_user_app(state,username,{"id":app_id,"name":app.get("name",app_id),"provider":"web-pwa","provider_id":provider.get("id"),"scope":"user","version":"web","installed_at":now,"launch_url":launch_url,"update_available":False},app_id);save_state(state)
            print(json.dumps({"ok":True,"message":"Web application added to LunarX","provider":"web-pwa","scope":"user","version":"web","launch_url":launch_url},separators=(",",":")));return
        if operation=="uninstall":
            put_state_user_app(state,username,None,app_id);save_state(state);print(json.dumps({"ok":True,"message":"Web application removed from LunarX","provider":"web-pwa","scope":"user"},separators=(",",":")));return
        print(json.dumps({"ok":True,"message":"Opening official web application","provider":"web-pwa","scope":"user","version":"web","launch_url":launch_url},separators=(",",":")));return
    if backend=="flatpak-user":
        if not Path("/usr/bin/flatpak").is_file():fail("Flatpak user provider is unavailable","APP_PROVIDER_UNAVAILABLE")
        ensure_flathub(record,environment)
        if operation=="install":args=["/usr/bin/flatpak","--user","install","--noninteractive","-y","flathub",app_id]
        elif operation=="update":args=["/usr/bin/flatpak","--user","update","--noninteractive","-y",app_id]
        elif operation=="uninstall":args=["/usr/bin/flatpak","--user","uninstall","--noninteractive","-y",app_id]
        else:
            displays=[]
            for socket_path in Path("/tmp/.X11-unix").glob("X*"):
                try:
                    if socket_path.stat().st_uid==record.pw_uid:displays.append(int(socket_path.name[1:]))
                except (OSError,ValueError):pass
            if not displays:fail("open the LunarX Desktop before launching a graphical application")
            launch_env=[item for item in environment if not item.startswith("DISPLAY=")]+[f"DISPLAY=:{max(displays)}"]
            subprocess.Popen(["/usr/sbin/runuser","--user",username,"--","/usr/bin/env",*launch_env,"/usr/bin/flatpak","run",app_id],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
            print(json.dumps({"ok":True,"message":"Application launch requested in the active Desktop","provider":"flatpak-user","scope":"user"},separators=(",",":")));return
        result=as_user(record,environment,args)
    elif backend=="ide-extension":
        if not Path("/usr/bin/flatpak").is_file():fail("VS Code Flatpak provider is unavailable","APP_PROVIDER_UNAVAILABLE")
        extension=str(app.get("extension_id",""))
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+",extension):fail("invalid extension identifier")
        ensure_flathub(record,environment)
        if operation not in {"install","update","uninstall"}:fail("IDE extensions are managed from the store, then launched inside VS Code")
        if operation=="uninstall":args=["/usr/bin/flatpak","run","--command=code","com.visualstudio.code","--uninstall-extension",extension]
        else:args=["/usr/bin/flatpak","run","--command=code","com.visualstudio.code","--install-extension",extension,"--force"]
        result=as_user(record,environment,args)
    elif backend in {"admin-apt-group","apt-system"}:
        if not is_lunarx_admin(username):fail("administrator role required")
        packages=provider.get("packages",app.get("packages",[]))
        if not isinstance(packages,list) or not packages or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+",str(item)) for item in packages):fail("invalid global package group")
        if operation in {"install","update"}:result=command(["/usr/bin/apt-get","install","-y",*(["--only-upgrade"] if operation=="update" and backend=="apt-system" else []),*[str(item) for item in packages]],timeout=1800)
        elif operation=="uninstall":result=command(["/usr/bin/apt-get","remove","-y",*[str(item) for item in packages]],timeout=1800)
        else:fail("this global package provider is not launchable on native Ubuntu")
        if result.returncode==0:
            if operation=="uninstall":state.setdefault("managed_apps",{}).pop(app_id,None)
            else:state.setdefault("managed_apps",{})[app_id]={"installed":True,"name":app.get("name",app_id),"provider":backend,"provider_id":provider.get("id"),"scope":"system","packages":packages,"version":", ".join(filter(None,(package_version(str(pkg)) for pkg in packages))) or "system","updated_at":int(time.time()),"update_available":False}
            save_state(state)
    else:fail("application backend is not automatically installable")
    if result.returncode:fail((result.stderr or result.stdout).decode("utf-8","replace")[-500:] or "application action failed")
    print(json.dumps({"ok":True,"message":f"{operation} completed","provider":backend,"scope":str(provider.get("scope") or "user")},separators=(",",":")))


def server_app(payload:dict[str,Any])->None:
    app_id=str(payload.get("app_id","")); op=str(payload.get("operation",""))
    if app_id!="lunarx-status-page" or op not in {"install","update","restart","remove"}:fail("application action not allow-listed")
    root=Path("/opt/lunarx-managed-apps/lunarx-status-page"); unit=Path("/etc/systemd/system/lunarx-status-page.service"); state=load_state(); current=state["server_apps"].get(app_id,{}); version=int(current.get("version",0))
    if op in {"install","update"}:
        root.mkdir(parents=True,exist_ok=True); os.chmod(root.parent,0o755); os.chmod(root,0o755); version=max(1,version+(1 if op=="update" else 0)); (root/"index.html").write_text(f"<!doctype html><title>LunarX Status</title><h1>LunarX Status</h1><p>managed app v{version}</p>\n"); os.chmod(root/"index.html",0o644)
        unit.write_text("[Unit]\nDescription=LunarX managed status page\nAfter=network.target\n[Service]\nUser=nobody\nWorkingDirectory=/opt/lunarx-managed-apps/lunarx-status-page\nExecStart=/usr/bin/python3 -m http.server 8799 --bind 127.0.0.1\nRestart=on-failure\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\n[Install]\nWantedBy=multi-user.target\n")
        command(["/usr/bin/systemctl","daemon-reload"]); command(["/usr/bin/systemctl","enable","--now","lunarx-status-page.service"]); state["server_apps"][app_id]={"installed":True,"version":version,"name":"LunarX Status Page"}
    elif op=="restart":
        if not unit.exists():fail("application is not installed")
        if command(["/usr/bin/systemctl","restart","lunarx-status-page.service"]).returncode:fail("application restart failed")
    else:
        command(["/usr/bin/systemctl","disable","--now","lunarx-status-page.service"]); unit.unlink(missing_ok=True); shutil.rmtree(root,ignore_errors=True); command(["/usr/bin/systemctl","daemon-reload"]); state["server_apps"].pop(app_id,None)
    save_state(state); active=command(["/usr/bin/systemctl","is-active","lunarx-status-page.service"]).stdout.decode().strip(); print(json.dumps({"ok":True,"app_id":app_id,"operation":op,"active":active,"state":state["server_apps"].get(app_id)},separators=(",",":")))

def main()->int:
    try:
        if os.geteuid()!=0:fail("root execution required")
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT:fail("request too large")
        try:payload=json.loads(raw.decode("utf-8"))
        except (ValueError,UnicodeDecodeError):fail("invalid request")
        if not isinstance(payload,dict):fail("object required")
        action=str(payload.get("action",""))
        if action=="authenticate":authenticate(payload)
        elif action=="create_user":create_user(payload)
        elif action=="update_user":update_user(payload)
        elif action=="delete_user":delete_user(payload)
        elif action=="list_users":list_users()
        elif action in {"status","restart_service"}:service_action({**payload,"operation":"restart" if action=="restart_service" else "status"})
        elif action=="storage_status":storage_status()
        elif action=="storage_loopback_test":loopback_test()
        elif action=="storage_register":storage_register(payload)
        elif action=="storage_grant":storage_grant(payload)
        elif action=="settings_update":settings_update(payload)
        elif action=="server_app":server_app(payload)
        elif action=="user_app_list":user_app_list(payload)
        elif action=="user_app":user_app(payload)
        elif action=="normalize_storage":normalize_storage(payload)
        elif action=="prepare_storage_directory":prepare_storage_directory(payload)
        elif action=="repair_user_storage":repair_user_storage(payload)
        elif action=="provision_existing_user":provision_existing_user(payload)
        elif action=="optimize_desktop":optimize_desktop(payload)
        elif action=="desktop_session":desktop_session(payload)
        elif action=="terminate_desktop":terminate_desktop(payload)
        else:fail("unsupported action")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok":False,"error":"privileged action could not be completed","error_code":"broker_failure","detail":str(exc)[:240]},separators=(",",":")))
        return 2
if __name__=="__main__":raise SystemExit(main())
