import io
import csv
import openpyxl
import unicodedata
import re
from datetime import datetime, date
from collections import defaultdict
from app.models import db
from app.models.student_model import Student, Parent
from app.models.financial_model import StudentFinancialAccount, FinancialAuditLog
from app.models.activity_log_model import log_activity


def normalize_name(s):
    if not s:
        return ""
    # Replace non-ascii replacement characters or question marks
    s = str(s).replace('\ufffd', 'o').replace('?', '')
    n = unicodedata.normalize('NFKD', s)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r'[^a-zA-Z0-9\s]', '', n)
    return " ".join(n.lower().split())


def room_to_grade(room):
    if not room:
        return "Unassigned"
    r = room.strip()
    if "Kindergarten" in r:
        return "Kindergarten"
    for i in range(1, 13):
        suffix = "th" if 4 <= i <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
        if f"{i}{suffix}" in r or f"Room {i}" in r or f"Grade {i}" in r:
            return f"{i}{suffix} Grade"
    cleaned = r.replace("Home Room ", "").strip()
    return cleaned if cleaned else "Unassigned"


def parse_dob(dob_val):
    if not dob_val:
        return None
    if isinstance(dob_val, datetime):
        return dob_val.date()
    if isinstance(dob_val, date):
        return dob_val
    s = str(dob_val).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%B %d, %Y', '%d %B, %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def clean_phone(p):
    if not p:
        return "555-0100"
    p_str = str(p).strip()
    # Keep digits and clean format
    digits = re.sub(r'\D', '', p_str)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits.startswith('1'):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    elif len(digits) > 10:
        return f"+{digits[:14]}"[:20]
    return (p_str[:20] if p_str else "555-0100")


def parse_family_rows(rows_iterator):
    """
    Parses child and parent directory rows from either Excel worksheet or CSV reader.
    Locates header row containing 'First Name', 'Last Name', and parent fields.
    """
    header_idx = None
    headers = []
    data_rows = []

    for idx, row in enumerate(rows_iterator):
        row_vals = [str(c).strip() if c is not None else "" for c in row]
        joined_lower = " ".join(row_vals).lower()
        if "first name" in joined_lower and "last name" in joined_lower and "dob" in joined_lower:
            header_idx = idx
            headers = [h.strip() for h in row_vals]
            continue
        
        if header_idx is not None and any(row_vals):
            data_rows.append(row)

    if header_idx is None:
        raise ValueError("Could not find header row containing 'First Name', 'Last Name', 'Dob'.")

    # Build column map
    col_map = {}
    for i, h in enumerate(headers):
        hl = h.lower().replace(" ", "").replace("_", "")
        if hl == "id":
            col_map['child_id'] = i
        elif hl == "firstname":
            col_map['first_name'] = i
        elif hl == "lastname":
            col_map['last_name'] = i
        elif hl == "room":
            col_map['room'] = i
        elif hl == "tags":
            col_map['tags'] = i
        elif hl == "studentid":
            col_map['student_id_number'] = i
        elif hl == "status":
            col_map['status'] = i
        elif hl == "dob":
            col_map['dob'] = i
        elif hl == "timeschedule":
            col_map['time_schedule'] = i
        elif hl == "city":
            col_map['city'] = i
        elif hl == "state":
            col_map['state'] = i
        elif hl == "zip":
            col_map['zip'] = i
        elif hl == "country":
            col_map['country'] = i

        # Parents 1 to 4
        for p_num in range(1, 5):
            prefix = f"parent{p_num}"
            if hl == f"{prefix}relation":
                col_map[f'p{p_num}_relation'] = i
            elif hl == f"{prefix}email":
                col_map[f'p{p_num}_email'] = i
            elif hl == f"{prefix}familyid":
                col_map[f'p{p_num}_family_id'] = i
            elif hl == f"{prefix}firstname":
                col_map[f'p{p_num}_first_name'] = i
            elif hl == f"{prefix}lastname":
                col_map[f'p{p_num}_last_name'] = i
            elif hl in (f"{prefix}mobilephone", f"{prefix}phone"):
                col_map[f'p{p_num}_phone'] = i
            elif hl == f"{prefix}pin":
                col_map[f'p{p_num}_pin'] = i

        # Pickup 1
        if hl == "pickup1relation":
            col_map['pickup1_relation'] = i
        elif hl == "pickup1firstname":
            col_map['pickup1_first_name'] = i
        elif hl == "pickup1lastname":
            col_map['pickup1_last_name'] = i
        elif hl in ("pickup1mobilephone", "pickup1phone"):
            col_map['pickup1_phone'] = i

    parsed_students = []

    for row in data_rows:
        def get_val(key):
            idx = col_map.get(key)
            if idx is not None and idx < len(row):
                v = row[idx]
                return v if v is not None else ""
            return ""

        fn = str(get_val('first_name')).strip()
        ln = str(get_val('last_name')).strip()
        if not (fn or ln):
            continue

        room = str(get_val('room')).strip()
        tags = str(get_val('tags')).strip()
        st_id = str(get_val('student_id_number')).strip()
        status_val = str(get_val('status')).strip() or "Active"
        raw_dob = get_val('dob')
        dob = parse_dob(raw_dob)

        city = str(get_val('city')).strip()
        state = str(get_val('state')).strip()
        zip_code = str(get_val('zip')).strip()
        country = str(get_val('country')).strip()

        parents = []
        for p_num in range(1, 5):
            p_email = str(get_val(f'p{p_num}_email')).strip().lower()
            p_fn = str(get_val(f'p{p_num}_first_name')).strip()
            p_ln = str(get_val(f'p{p_num}_last_name')).strip()
            p_phone = clean_phone(get_val(f'p{p_num}_phone'))
            p_rel = str(get_val(f'p{p_num}_relation')).strip()
            p_pin = str(get_val(f'p{p_num}_pin')).strip()
            p_fam_id = str(get_val(f'p{p_num}_family_id')).strip()

            if p_email or p_fn or p_ln:
                # If first name contains both names and last name is blank, split
                if p_fn and not p_ln and ' ' in p_fn:
                    parts = p_fn.split()
                    p_fn = parts[0]
                    p_ln = " ".join(parts[1:])
                elif not p_fn and not p_ln and p_email:
                    parts = p_email.split('@')[0].split('.')
                    p_fn = parts[0].capitalize()
                    p_ln = parts[1].capitalize() if len(parts) > 1 else ln

                parents.append({
                    'index': p_num,
                    'relation': p_rel or "Parent/Guardian",
                    'email': p_email,
                    'first_name': p_fn or "Parent",
                    'last_name': p_ln or ln,
                    'phone': p_phone,
                    'pin': p_pin or "2963",
                    'family_id': p_fam_id
                })

        parsed_students.append({
            'child_id': str(get_val('child_id')).strip(),
            'first_name': fn,
            'last_name': ln,
            'grade_level': room_to_grade(room),
            'room': room,
            'tags': tags,
            'student_id_number': st_id,
            'status': status_val.capitalize() if status_val.lower() == 'active' else 'Active',
            'date_of_birth': dob.isoformat() if dob else None,
            'city': city,
            'state': state,
            'zip': zip_code,
            'country': country,
            'parents': parents
        })

    return parsed_students


def parse_family_excel_file(file_content_or_path):
    if isinstance(file_content_or_path, (bytes, bytearray)):
        wb = openpyxl.load_workbook(io.BytesIO(file_content_or_path), data_only=True)
    elif isinstance(file_content_or_path, str):
        if not file_content_or_path.endswith(('.xlsx', '.xlsm', '.xltx')):
            wb = openpyxl.load_workbook(io.BytesIO(file_content_or_path.encode('utf-8', errors='replace')), data_only=True)
        else:
            wb = openpyxl.load_workbook(file_content_or_path, data_only=True)
    else:
        wb = openpyxl.load_workbook(file_content_or_path, data_only=True)

    ws = wb.active
    rows = []
    for r in range(1, ws.max_row + 1):
        vals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        rows.append(vals)

    return parse_family_rows(rows)


def parse_family_csv_file(file_content_or_path):
    if isinstance(file_content_or_path, str) and not file_content_or_path.endswith('.csv'):
        f = io.StringIO(file_content_or_path)
    elif isinstance(file_content_or_path, bytes):
        f = io.StringIO(file_content_or_path.decode('utf-8', errors='replace'))
    else:
        f = open(file_content_or_path, 'r', encoding='utf-8', errors='replace')

    reader = csv.reader(f)
    rows = list(reader)
    if hasattr(f, 'close') and f != file_content_or_path:
        f.close()
    return parse_family_rows(rows)


def load_and_parse_family_file(file_obj, filename=""):
    fn = filename.lower()
    if fn.endswith('.csv') or fn.endswith('.txt'):
        content = file_obj.read()
        return parse_family_csv_file(content)
    else:
        content = file_obj.read()
        return parse_family_excel_file(content)


def preview_family_import(students_data):
    """
    Compares the parsed students & families against the live database,
    providing matching stats and a preview of changes.
    """
    existing_students = Student.query.all()
    # Map normalized name -> student object
    student_map = {}
    for s in existing_students:
        norm_key = normalize_name(f"{s.first_name} {s.last_name}")
        student_map[norm_key] = s

    existing_parents = Parent.query.all()
    parent_email_map = {p.email.lower(): p for p in existing_parents if p.email}
    
    # Track placeholder/dummy parents created by plan importer
    dummy_parent_count = sum(1 for p in existing_parents if p.email and p.email.endswith('@parent.elaaschool.org'))

    matched_students = 0
    new_students = 0
    real_parent_emails = set()
    parents_to_upgrade = 0
    unique_families = set()

    preview_list = []

    for s_info in students_data:
        fn = s_info['first_name']
        ln = s_info['last_name']
        norm_k = normalize_name(f"{fn} {ln}")
        matched_s = student_map.get(norm_k)

        has_dummy_parent = False
        if matched_s:
            matched_students += 1
            has_dummy_parent = any(p.email and p.email.endswith('@parent.elaaschool.org') for p in matched_s.parents)
        else:
            new_students += 1

        p_emails_for_student = []
        for p in s_info.get('parents', []):
            if p.get('email'):
                real_parent_emails.add(p['email'])
                p_emails_for_student.append(p['email'])
            if p.get('family_id'):
                unique_families.add(p['family_id'])

        if has_dummy_parent and p_emails_for_student:
            parents_to_upgrade += 1

        if len(preview_list) < 150:
            preview_list.append({
                'first_name': fn,
                'last_name': ln,
                'grade_level': s_info['grade_level'],
                'room': s_info['room'],
                'date_of_birth': s_info['date_of_birth'],
                'is_matched': bool(matched_s),
                'matched_student_id': matched_s.id if matched_s else None,
                'has_dummy_parent': has_dummy_parent,
                'parent_count': len(s_info.get('parents', [])),
                'parent_names': ", ".join(f"{p['first_name']} {p['last_name']} ({p.get('relation') or 'Parent'})" for p in s_info.get('parents', [])),
                'parent_emails': ", ".join(p['email'] for p in s_info.get('parents', []) if p['email']) or "No email provided"
            })

    return {
        'total_students_in_file': len(students_data),
        'matched_students_count': matched_students,
        'new_students_count': new_students,
        'unique_real_parent_emails': len(real_parent_emails),
        'dummy_parents_in_system': dummy_parent_count,
        'students_with_dummy_parents_to_upgrade': parents_to_upgrade,
        'unique_families_count': len(unique_families),
        'preview_students': preview_list
    }


def execute_family_import(students_data, options=None, actor=None):
    """
    Executes the reconciliation & import of students and parents:
    - Replaces placeholder dummy emails with real parent emails and phones
    - Links siblings under shared real parent accounts
    - Updates real date_of_birth and grade levels for students
    - Provisions remaining active students who didn't have tuition plans
    """
    if options is None:
        options = {}

    update_existing = options.get('update_existing_students', True)
    create_missing = options.get('create_missing_students', True)
    upgrade_dummy_parents = options.get('upgrade_dummy_parents', True)
    cleanup_dummy = options.get('cleanup_unmatched_dummy_parents', False)

    today = date.today()
    results = {
        'students_updated': 0,
        'students_created': 0,
        'parents_created': 0,
        'parents_upgraded': 0,
        'parents_linked': 0,
        'dummy_accounts_cleaned': 0,
        'errors': []
    }

    # Fetch live database state
    existing_students = Student.query.all()
    student_map = {}
    for s in existing_students:
        norm_key = normalize_name(f"{s.first_name} {s.last_name}")
        student_map[norm_key] = s

    # Map existing parents by email
    existing_parents = Parent.query.all()
    parent_by_email = {}
    for p in existing_parents:
        if p.email:
            parent_by_email[p.email.lower().strip()] = p

    for s_info in students_data:
        fn = s_info['first_name']
        ln = s_info['last_name']
        norm_k = normalize_name(f"{fn} {ln}")
        student = student_map.get(norm_k)

        dob = parse_dob(s_info.get('date_of_birth')) or today

        # 1. Student Provision / Update
        if student:
            if update_existing:
                student.status = 'Active'
                if s_info.get('grade_level'):
                    student.grade_level = s_info['grade_level']
                if dob:
                    student.date_of_birth = dob
                if s_info.get('student_id_number'):
                    student.student_id_number = s_info['student_id_number']

                # Format address / notes
                addr_parts = [s_info.get('city'), s_info.get('state'), s_info.get('zip')]
                addr_str = ", ".join(p for p in addr_parts if p)
                if addr_str and ("Address:" not in (student.notes or "")):
                    student.notes = (student.notes or "") + f"\nAddress: {addr_str}"

                results['students_updated'] += 1
        elif create_missing:
            # Create new student
            student = Student(
                first_name=fn,
                last_name=ln,
                grade_level=s_info.get('grade_level') or "Unassigned",
                date_of_birth=dob,
                status='Active',
                student_id_number=s_info.get('student_id_number') or None,
                enrollment_date=today,
                notes=f"Imported from Child & Family Directory. City: {s_info.get('city', '')}."
            )
            db.session.add(student)
            db.session.flush()

            # Ensure financial account exists
            fin_acct = StudentFinancialAccount(student_id=student.id)
            db.session.add(fin_acct)
            db.session.flush()

            student_map[norm_k] = student
            results['students_created'] += 1
        else:
            continue

        # 2. Reconcile Parents for this student
        raw_parents = s_info.get('parents', [])
        for p_data in raw_parents:
            real_email = (p_data.get('email', '') or '').strip().lower()[:120]
            p_fn = ((p_data.get('first_name', '') or '').strip() or "Parent")[:100]
            p_ln = ((p_data.get('last_name', '') or '').strip() or ln or "Guardian")[:100]
            p_phone = clean_phone(p_data.get('phone', ''))[:20]
            p_pin = str(p_data.get('pin', '') or "2963").strip()[:10]

            # Check if parent with this real email already exists in DB
            target_parent = parent_by_email.get(real_email) if real_email else None

            if target_parent:
                # Update phone & PIN if empty or placeholder
                if not target_parent.phone or target_parent.phone == "555-0100":
                    target_parent.phone = p_phone
                if p_pin and p_pin != "2963":
                    target_parent.sign_in_pin = p_pin
                
                # Link student if not linked
                if student not in target_parent.children:
                    target_parent.children.append(student)
                    results['parents_linked'] += 1
            elif real_email:
                # Check if student currently has a dummy placeholder parent to upgrade
                dummy_to_upgrade = None
                if upgrade_dummy_parents:
                    for cur_p in student.parents:
                        if cur_p.email and cur_p.email.endswith('@parent.elaaschool.org'):
                            dummy_to_upgrade = cur_p
                            break

                if dummy_to_upgrade:
                    # Upgrade dummy account in place
                    old_email = dummy_to_upgrade.email
                    dummy_to_upgrade.email = real_email
                    dummy_to_upgrade.first_name = p_fn
                    dummy_to_upgrade.last_name = p_ln
                    dummy_to_upgrade.phone = p_phone
                    dummy_to_upgrade.sign_in_pin = p_pin
                    dummy_to_upgrade.is_active = True

                    parent_by_email[real_email] = dummy_to_upgrade
                    results['parents_upgraded'] += 1
                else:
                    # Create brand new parent with real email
                    new_p = Parent(
                        first_name=p_fn,
                        last_name=p_ln,
                        email=real_email,
                        phone=p_phone,
                        sign_in_pin=p_pin,
                        is_active=True
                    )
                    db.session.add(new_p)
                    db.session.flush()

                    new_p.children.append(student)
                    parent_by_email[real_email] = new_p
                    results['parents_created'] += 1
            else:
                # Parent without email - record in student notes for contact tracing
                contact_note = f"Contact: {p_fn} {p_ln} ({p_data.get('relation', 'Parent')}) - Tel: {p_phone}"
                if contact_note not in (student.notes or ""):
                    student.notes = (student.notes or "") + f"\n{contact_note}"

        # If student now has at least one real parent linked, remove any remaining dummy parents on this student
        if upgrade_dummy_parents:
            real_linked = [p for p in student.parents if p.email and not p.email.endswith('@parent.elaaschool.org')]
            if real_linked:
                remaining_dummies = [p for p in list(student.parents) if p.email and p.email.endswith('@parent.elaaschool.org')]
                for d_p in remaining_dummies:
                    student.parents.remove(d_p)
                    if len(d_p.children) == 0:
                        db.session.delete(d_p)
                        results['dummy_accounts_cleaned'] += 1

    # 3. Optional: Clean up remaining unlinked dummy parents
    if cleanup_dummy:
        all_parents_refresh = Parent.query.all()
        for p in all_parents_refresh:
            if p.email and p.email.endswith('@parent.elaaschool.org'):
                # Check if it has no children or if all its children have other real parents
                if len(p.children) == 0:
                    db.session.delete(p)
                    results['dummy_accounts_cleaned'] += 1

    if actor:
        log_activity(
            actor,
            f"Imported Student & Family Directory: {results['students_updated']} student(s) updated, "
            f"{results['students_created']} student(s) created, {results['parents_upgraded']} parent email(s) upgraded, "
            f"{results['parents_created']} real parent account(s) created."
        )

    db.session.commit()
    return results
