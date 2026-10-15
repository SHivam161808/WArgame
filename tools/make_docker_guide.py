"""Build docs/Docker_Setup_Guide.pdf — the step-by-step Docker guide for Windows and Linux players.

Needs reportlab (BSD), which is *not* a game dependency:
    python -m venv /tmp/pdfenv && /tmp/pdfenv/bin/pip install reportlab
    /tmp/pdfenv/bin/python tools/make_docker_guide.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'Docker_Setup_Guide.pdf'
FONTS = Path('/usr/share/fonts/truetype/dejavu')
pdfmetrics.registerFont(TTFont('Sans', str(FONTS / 'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('Sans-Bold', str(FONTS / 'DejaVuSans-Bold.ttf')))
pdfmetrics.registerFont(TTFont('Mono', str(FONTS / 'DejaVuSansMono.ttf')))
pdfmetrics.registerFontFamily('Sans', normal='Sans', bold='Sans-Bold', italic='Sans', boldItalic='Sans-Bold')

INK = colors.HexColor('#1d2430')
MUTED = colors.HexColor('#5b6576')
ACCENT = colors.HexColor('#1f7a4d')
CODE_BG = colors.HexColor('#f2f4f7')
RULE = colors.HexColor('#d5dae1')
WARN_BG = colors.HexColor('#fff6e0')
WARN_RULE = colors.HexColor('#e0b44c')
TIP_BG = colors.HexColor('#e8f4ee')

S = dict(
    title=ParagraphStyle('title', fontName='Sans-Bold', fontSize=24, leading=30, textColor=INK, alignment=TA_CENTER),
    sub=ParagraphStyle('sub', fontName='Sans', fontSize=11.5, leading=16, textColor=MUTED, alignment=TA_CENTER),
    h1=ParagraphStyle('h1', fontName='Sans-Bold', fontSize=16, leading=21, textColor=ACCENT, spaceBefore=10, spaceAfter=6),
    h2=ParagraphStyle('h2', fontName='Sans-Bold', fontSize=12, leading=16, textColor=INK, spaceBefore=8, spaceAfter=3),
    body=ParagraphStyle('body', fontName='Sans', fontSize=10, leading=14.5, textColor=INK, spaceAfter=5),
    small=ParagraphStyle('small', fontName='Sans', fontSize=8.8, leading=12, textColor=MUTED),
    cell=ParagraphStyle('cell', fontName='Sans', fontSize=9.2, leading=12.5, textColor=INK),
    cellb=ParagraphStyle('cellb', fontName='Sans-Bold', fontSize=9.2, leading=12.5, textColor=INK),
    code=ParagraphStyle('code', fontName='Mono', fontSize=9, leading=12.5, textColor=INK),
)
W = A4[0] - 40 * mm


def p(text, style='body'):
    return Paragraph(text, S[style])


def code(text):
    t = Table([[Preformatted(text.strip('\n'), S['code'])]], colWidths=[W])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), CODE_BG), ('BOX', (0, 0), (-1, -1), 0.5, RULE),
                           ('LEFTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 6),
                           ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    return KeepTogether([t, Spacer(1, 6)])


def box(text, kind='tip'):
    bg, rule, label = (TIP_BG, ACCENT, 'Tip') if kind == 'tip' else (WARN_BG, WARN_RULE, 'Important')
    t = Table([[p(f'<b>{label}:</b> {text}', 'cell')]], colWidths=[W])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), bg), ('LINEBEFORE', (0, 0), (0, -1), 3, rule),
                           ('LEFTPADDING', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, -1), 6),
                           ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    return KeepTogether([t, Spacer(1, 7)])


def table(rows, widths, head=True):
    data = [[p(c, 'cellb' if head and i == 0 else 'cell') for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[W * f for f in widths], repeatRows=1 if head else 0)
    st = [('GRID', (0, 0), (-1, -1), 0.4, RULE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
          ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]
    if head:
        st.append(('BACKGROUND', (0, 0), (-1, 0), CODE_BG))
    t.setStyle(TableStyle(st))
    return KeepTogether([t, Spacer(1, 8)])


def step(n, title, *items):
    return [p(f'<font color="#1f7a4d">Step {n}.</font> {title}', 'h2'), *items]


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Sans', 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm, 'Armoured Battle Console — Docker setup guide')
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f'Page {doc.page}')
    canvas.restoreState()


story = [
    Spacer(1, 30 * mm),
    p('Armoured Battle Console', 'title'),
    Spacer(1, 4),
    p('Run the game on Windows or Linux with Docker', 'sub'),
    Spacer(1, 18 * mm),
    p('<b>What this guide gives you:</b> the full game — Village Battle (10 v 10 with computer players) and the '
      'Armoured Battle — running on your own computer with <b>one command</b>. Docker brings its own database, so you '
      'do not install or configure PostgreSQL, Python or anything else by hand.'),
    Spacer(1, 6),
    table([
        ['You need', 'Details'],
        ['A 64-bit computer', 'Windows 10 (22H2) or Windows 11, or any modern Linux (Ubuntu 22.04+, Debian 12, Fedora …)'],
        ['8 GB RAM, 5 GB free disk', 'The first start downloads about 1 GB (Python and PostgreSQL images)'],
        ['Internet for the first start', 'After that the game runs offline on your network'],
        ['A browser with WebGL', 'Chrome, Edge or Firefox (up to date). A real graphics card makes the 3D view smooth'],
        ['The game folder', 'The <font name="Mono">wargame</font> project folder (zip or git clone) with '
                            '<font name="Mono">Dockerfile</font> and <font name="Mono">docker-compose.yml</font> inside'],
    ], [0.3, 0.7]),
    p('Contents: 1 Install Docker (Windows) · 2 Install Docker (Linux) · 3 Start the game · 4 Play · '
      '5 Play with friends on your network · 6 Everyday commands · 7 Settings · 8 Troubleshooting', 'small'),
    PageBreak(),

    # ── Windows ────────────────────────────────────────────────────────────
    p('1  Install Docker on Windows', 'h1'),
    *step(1, 'Turn on virtualisation',
          p('Open <b>Task Manager → Performance → CPU</b>. If it shows <b>Virtualization: Enabled</b>, go to step 2. '
            'If it says Disabled, restart into your BIOS/UEFI (usually F2, F10, Del or Esc during boot) and enable '
            '<b>Intel VT-x</b> / <b>AMD-V (SVM)</b>.')),
    *step(2, 'Install WSL 2',
          p('Open <b>PowerShell as Administrator</b> (right-click Start → Terminal (Admin)) and run:'),
          code('wsl --install'),
          p('Restart the computer when it asks. If WSL is already installed, run <font name="Mono">wsl --update</font> instead.')),
    *step(3, 'Install Docker Desktop',
          p('Download <b>Docker Desktop for Windows</b> from <font name="Mono">https://www.docker.com/products/docker-desktop/</font> '
            'and run the installer. Keep <b>“Use WSL 2 instead of Hyper-V”</b> ticked. Restart, then start Docker Desktop '
            'from the Start menu and wait until the whale icon in the taskbar says <b>Docker Desktop is running</b>.'),
          box('Docker Desktop is free for personal use, education and small companies (fewer than 250 employees '
              'and less than $10 million revenue). Larger companies need a paid Docker subscription.', 'warn')),
    *step(4, 'Check it works',
          p('Open a new PowerShell window (normal, not admin) and run:'),
          code('docker --version\ndocker compose version'),
          p('Both commands should print a version number.')),

    # ── Linux ──────────────────────────────────────────────────────────────
    p('2  Install Docker on Linux', 'h1'),
    p('On Ubuntu / Debian / Fedora the official script installs Docker Engine and the Compose plugin:'),
    code('curl -fsSL https://get.docker.com | sudo sh\n'
         'sudo usermod -aG docker $USER      # run docker without sudo\n'
         'newgrp docker                      # or log out and back in'),
    p('Check it works:'),
    code('docker --version\ndocker compose version\ndocker run --rm hello-world'),
    box('Use <font name="Mono">docker compose</font> (with a space). The old <font name="Mono">docker-compose</font> '
        '(with a dash) is version 1 and does not understand this project\'s file.'),
    PageBreak(),

    # ── Start ──────────────────────────────────────────────────────────────
    p('3  Start the game', 'h1'),
    *step(1, 'Get the game folder',
          p('Unzip the project or clone it. Put it somewhere simple, for example '
            '<font name="Mono">C:\\Games\\wargame</font> on Windows or <font name="Mono">~/wargame</font> on Linux.')),
    *step(2, 'Open a terminal in that folder',
          p('<b>Windows:</b> open the folder in File Explorer, right-click an empty area → <b>Open in Terminal</b>. '
            '<b>Linux:</b> <font name="Mono">cd ~/wargame</font>.'),
          p('You should see <font name="Mono">docker-compose.yml</font> when you list the files '
            '(<font name="Mono">dir</font> on Windows, <font name="Mono">ls</font> on Linux).')),
    *step(3, 'Start everything',
          code('docker compose up -d --build'),
          p('The first time this takes 3–10 minutes: Docker downloads Python and PostgreSQL and builds the game image. '
            'Later starts take a few seconds. Watch the start-up with:'),
          code('docker compose logs -f web'),
          p('When you see <font name="Mono">[wargame] ready → open http://localhost:8000</font> the game is up. '
            'Press <b>Ctrl+C</b> to stop watching the log (the game keeps running).')),
    *step(4, 'Open the game',
          p('Go to <b><font name="Mono">http://localhost:8000</font></b> in Chrome, Edge or Firefox.')),
    box('There is no database to set up. PostgreSQL runs in its own container and keeps its data in a Docker volume '
        '(<font name="Mono">wargame_pgdata</font>), so your matches and settings survive restarts and reboots.'),

    # ── Play ───────────────────────────────────────────────────────────────
    p('4  Play', 'h1'),
    p('<b>1.</b> On the home page click <b>Log in to start a Village Battle</b> and sign in with '
      'user <font name="Mono">admin</font>, password <font name="Mono">admin123</font> (change it — see section 7).'),
    p('<b>2.</b> Click <b>Play Village Battle</b>. A new village is built and you join BLUE. 10 RED computer players '
      'hunt you; every empty BLUE place is filled by a computer team-mate.'),
    p('<b>3.</b> Press <b>READY</b> in the lobby. Loot guns from the houses, grab a bike or car, and fight. '
      'Last squad standing wins.'),
    table([
        ['Key', 'Action', 'Key', 'Action'],
        ['W A S D', 'Move', 'Mouse', 'Look / aim'],
        ['Shift', 'Sprint', 'Left click', 'Shoot'],
        ['C', 'Crouch', 'Right click (hold)', 'Aim down sights'],
        ['1 – 3', 'Switch gun', 'V', 'First / third person'],
        ['F', 'Get in / out of a vehicle', 'Enter', 'Team chat'],
    ], [0.17, 0.33, 0.2, 0.3]),
    p('The Armoured Battle (tanks on real border terrain) is in the <b>Command Panel</b> after you log in.', 'small'),
    PageBreak(),

    # ── LAN ────────────────────────────────────────────────────────────────
    p('5  Play with friends on your network', 'h1'),
    p('Friends on the same Wi-Fi / LAN can join your game from their own browser — they do not need Docker.'),
    *step(1, 'Find your computer\'s IP address',
          p('<b>Windows:</b> run <font name="Mono">ipconfig</font> and look for <b>IPv4 Address</b> (for example '
            '<font name="Mono">192.168.1.25</font>). <b>Linux:</b> run <font name="Mono">hostname -I</font>.')),
    *step(2, 'Share the address, Room ID and key',
          p('When you start a Village Battle a message shows the <b>Room ID</b> and the <b>BLUE key</b>. Your friends open '
            '<font name="Mono">http://192.168.1.25:8000</font> (your IP), type the Room ID, the key and a callsign, '
            'and click <b>Enter battle</b>. Up to 10 humans per side; computer players take every empty place.')),
    *step(3, 'Allow it through the firewall (if friends cannot connect)',
          p('<b>Windows:</b> when Windows asks whether Docker Desktop may use the network, allow <b>Private networks</b>. '
            'Or run in an admin PowerShell:'),
          code('New-NetFirewallRule -DisplayName "Wargame 8000" -Direction Inbound `\n'
               '    -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private'),
          p('<b>Linux with ufw:</b> <font name="Mono">sudo ufw allow 8000/tcp</font>')),
    box('Only share the game on networks you trust. It is meant for a home or office LAN, not the open internet.', 'warn'),

    # ── Commands ───────────────────────────────────────────────────────────
    p('6  Everyday commands', 'h1'),
    p('Run these in the game folder (the one with <font name="Mono">docker-compose.yml</font>).'),
    table([
        ['What you want', 'Command'],
        ['Start the game', '<font name="Mono">docker compose up -d</font>'],
        ['Stop the game (keeps all data)', '<font name="Mono">docker compose down</font>'],
        ['Is it running?', '<font name="Mono">docker compose ps</font>'],
        ['See the game log', '<font name="Mono">docker compose logs -f web</font>'],
        ['Restart after a crash', '<font name="Mono">docker compose restart web</font>'],
        ['Update after new code', '<font name="Mono">docker compose up -d --build</font>'],
        ['Wipe everything and start fresh', '<font name="Mono">docker compose down -v</font>  then  <font name="Mono">docker compose up -d --build</font>'],
    ], [0.38, 0.62]),
    box('<font name="Mono">down -v</font> deletes the database volume: all matches, replays and the admin password '
        'are gone. Only use it when you really want a clean start.', 'warn'),
    p('The game starts by itself when Docker starts (<font name="Mono">restart: unless-stopped</font>). On Windows, '
      'enable <b>Docker Desktop → Settings → General → Start Docker Desktop when you sign in</b> if you want that '
      'after every reboot.'),
    PageBreak(),

    # ── Settings ───────────────────────────────────────────────────────────
    p('7  Settings (optional)', 'h1'),
    p('Create a file called <font name="Mono">.env</font> in the game folder (next to '
      '<font name="Mono">docker-compose.yml</font>) with any of these lines, then run '
      '<font name="Mono">docker compose up -d</font> again:'),
    code('ADMIN_USER=admin\nADMIN_PASSWORD=MyStrongPassword\nWARGAME_PORT=8000'),
    table([
        ['Setting', 'What it does'],
        ['<font name="Mono">ADMIN_PASSWORD</font>', 'Password of the admin account. Used only when that account is first '
                                                    'created; to change it later run <font name="Mono">docker compose exec web python manage.py changepassword admin</font>'],
        ['<font name="Mono">WARGAME_PORT</font>', 'Port on your computer. Use e.g. 8080 if something else already uses 8000; '
                                                  'then open <font name="Mono">http://localhost:8080</font>'],
        ['<font name="Mono">OFFLINE_TERRAIN=1</font>', 'Use only the elevation tiles that come with the game (no internet needed for '
                                                       'the armoured theatres)'],
        ['<font name="Mono">LLM_BASE_URL</font>', 'Optional self-hosted AI model (Ollama etc.) for the AI advisor. Inside Docker, '
                                                  'reach Ollama on your computer at <font name="Mono">http://host.docker.internal:11434</font>'],
    ], [0.3, 0.7]),
    p('The database address is fixed inside <font name="Mono">docker-compose.yml</font> on purpose, so a '
      '<font name="Mono">DATABASE_URL</font> line in <font name="Mono">.env</font> from a non-Docker setup cannot '
      'break the Docker setup.', 'small'),

    # ── Troubleshooting ────────────────────────────────────────────────────
    p('8  Troubleshooting', 'h1'),
    table([
        ['Problem', 'Fix'],
        ['<b>“docker: command not found”</b> or <b>“cannot connect to the Docker daemon”</b>',
         'Docker Desktop is not running (Windows) — start it and wait for the whale icon. On Linux: '
         '<font name="Mono">sudo systemctl start docker</font>, and make sure you logged out/in after '
         '<font name="Mono">usermod -aG docker</font>.'],
        ['<b>“WSL 2 installation is incomplete”</b> / Docker Desktop will not start',
         'Run <font name="Mono">wsl --update</font> in an admin PowerShell, restart, and check virtualisation is enabled '
         '(section 1, step 1).'],
        ['<b>“port is already allocated”</b> / “address already in use”',
         'Something else uses port 8000. Set <font name="Mono">WARGAME_PORT=8080</font> in <font name="Mono">.env</font> '
         '(section 7) and run <font name="Mono">docker compose up -d</font>.'],
        ['The page does not open right after starting',
         'The first start migrates and seeds the database (about a minute). Watch '
         '<font name="Mono">docker compose logs -f web</font> until it says <b>ready</b>.'],
        ['<b>“could not reach the database”</b> in the log',
         'Run <font name="Mono">docker compose ps</font>. If <font name="Mono">db</font> is not healthy, see '
         '<font name="Mono">docker compose logs db</font>. As a last resort wipe and restart (section 6).'],
        ['<b>“exec docker/entrypoint.sh: no such file”</b> on Windows',
         'The file got Windows line endings. The image already fixes this; rebuild with '
         '<font name="Mono">docker compose build --no-cache web</font>.'],
        ['The 3D view is black or very slow',
         'Update the browser and graphics driver. In Chrome check <font name="Mono">chrome://gpu</font> shows WebGL '
         'as Hardware accelerated. Close other heavy tabs.'],
        ['Friends cannot join', 'Use your LAN IP, not localhost; allow port 8000 in the firewall (section 5).'],
        ['Still stuck', 'Send the output of <font name="Mono">docker compose ps</font> and '
                        '<font name="Mono">docker compose logs --tail 100 web</font> to whoever looks after the game.'],
    ], [0.36, 0.64]),
]

OUT.parent.mkdir(exist_ok=True)
doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                        bottomMargin=20 * mm, title='Armoured Battle Console — Docker setup guide',
                        author='Armoured Battle Console team', subject='Run the game on Windows or Linux with Docker')
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)
