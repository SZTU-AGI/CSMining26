from pathlib import Path

p = Path('evidence_reasoning_v2.py')
s = p.read_text(encoding='utf-8')

start = s.index('def normalize_contract_atoms(')
end = s.index('\ndef chunk_id(', start)

replacement = r'''def _walk_contract(value, path=()):
    """Yield (path, value) for every node in a JSON-like contract."""
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_contract(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_contract(child, path + (str(index),))


def _looks_like_atom_definition(value):
    if not isinstance(value, dict):
        return False
    descriptive_keys = {
        'proposition', 'display_text', 'criterion_quote',
        'basis_candidate_ids', 'basis_ids', 'legal_basis_ids',
        'relations', 'text',
    }
    return bool(descriptive_keys.intersection(value.keys()))


def normalize_contract_atoms(contract: dict) -> dict[str, dict]:
    """Discover atom definitions recursively without assuming a fixed schema."""
    atoms = {}
    atom_id_re = re.compile(r'^A\d+$')
    explicit_container_keys = {
        'atoms', 'atom_catalog', 'atom_definitions', 'scoring_atoms',
        'propositions', 'proposition_catalog',
    }

    for path, value in _walk_contract(contract):
        if not path or path[-1] not in explicit_container_keys:
            continue
        if isinstance(value, dict):
            for key, child in value.items():
                atom_id = str(key).strip()
                if not atom_id_re.fullmatch(atom_id) or not isinstance(child, dict):
                    continue
                atom = dict(child)
                atom.setdefault('atom_id', atom_id)
                atoms.setdefault(atom_id, atom)
        elif isinstance(value, list):
            for child in value:
                if not isinstance(child, dict):
                    continue
                atom_id = str(
                    child.get('atom_id') or child.get('id')
                    or child.get('proposition_id') or ''
                ).strip()
                if not atom_id_re.fullmatch(atom_id):
                    continue
                if not _looks_like_atom_definition(child):
                    continue
                atom = dict(child)
                atom.setdefault('atom_id', atom_id)
                atoms.setdefault(atom_id, atom)

    for _path, value in _walk_contract(contract):
        if not isinstance(value, dict):
            continue
        for key, child in value.items():
            atom_id = str(key).strip()
            if not atom_id_re.fullmatch(atom_id):
                continue
            if not _looks_like_atom_definition(child):
                continue
            atom = dict(child)
            atom.setdefault('atom_id', atom_id)
            atoms.setdefault(atom_id, atom)

    for _path, value in _walk_contract(contract):
        if not _looks_like_atom_definition(value):
            continue
        atom_id = str(value.get('atom_id') or value.get('id') or '').strip()
        if not atom_id_re.fullmatch(atom_id):
            continue
        atom = dict(value)
        atom.setdefault('atom_id', atom_id)
        atoms.setdefault(atom_id, atom)

    return atoms


def find_contract_component(contract: dict, keys: tuple[str, ...]):
    for _path, value in _walk_contract(contract):
        if not isinstance(value, dict):
            continue
        for key in keys:
            if key in value and value[key] is not None:
                return value[key]
    return None


def normalized_contract_view(contract: dict) -> dict:
    return {
        'atoms': normalize_contract_atoms(contract),
        'satisfaction': find_contract_component(
            contract, ('satisfaction', 'satisfaction_root', 'satisfaction_ast')
        ),
        'logic_basis': find_contract_component(
            contract, ('logic_basis', 'logic_bases')
        ) or [],
    }


def describe_contract_shape(contract: dict) -> dict:
    atom_refs = []
    for path, value in _walk_contract(contract):
        if not isinstance(value, dict):
            continue
        atom_id = value.get('atom_id')
        if isinstance(atom_id, str) and re.fullmatch(r'A\d+', atom_id):
            atom_refs.append({
                'path': '.'.join(path) or '<root>',
                'atom_id': atom_id,
                'keys': sorted(value.keys()),
            })
    view = normalized_contract_view(contract)
    return {
        'top_level_keys': sorted(contract.keys()),
        'discovered_atom_ids': sorted(view['atoms'].keys()),
        'atom_reference_locations': atom_refs[:30],
        'satisfaction': view['satisfaction'],
        'logic_basis': view['logic_basis'],
    }

'''

s = s[:start] + replacement + s[end:]

old = '''        "frozen_contract": {
            "atoms": contract.get("atoms", []),
            "satisfaction": contract.get("satisfaction"),
            "logic_basis": contract.get("logic_basis", []),
        },'''
new = '''        "frozen_contract": normalized_contract_view(contract),'''

if old in s:
    s = s.replace(old, new, 1)
elif '"frozen_contract": normalized_contract_view(contract),' not in s:
    raise SystemExit('Could not find frozen_contract prompt block')

p.write_text(s, encoding='utf-8')
print('Installed recursive contract schema adapter.')
