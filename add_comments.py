#!/usr/bin/env python3
"""Script zum automatischen Hinzufügen von dummsicheren Kommentaren."""

import os
import re

def add_header_comments(filepath, content):
    """Fügt Header-Kommentar hinzu wenn fehlt."""
    filename = os.path.basename(filepath)
    
    header_template = f'''# ================================================================================
# DATEI: {filename}
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

'''
    
    # Prüfen ob schon ein ähnlicher Header existiert
    if '# ===' in content[:500] and ('DATEI:' in content[:500] or 'PROJEKT:' in content[:500]):
        return content
    
    # Header einfügen nach shebang falls vorhanden
    if content.startswith('#!/'):
        lines = content.split('\n', 1)
        return lines[0] + '\n' + header_template + (lines[1] if len(lines) > 1 else '')
    
    return header_template + content

def enhance_function_comments(content):
    # -------------------------------------------------------------------------
    # FUNKTION: enhance_function_comments
    # PARAMETER: content
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Verbessert Kommentare für Funktionen und Klassen."""
    
    # Muster für Funktionen ohne gute Docstrings
    func_pattern = r'(async )?def (\w+)\(([^)]*)\):'
    
    def replace_func(match):
        async_kw = match.group(1) or ''
        func_name = match.group(2)
        params = match.group(3)
        
        original = match.group(0)
        
        # Prüfen ob schon ein guter Kommentar da ist
        start_pos = match.start()
        context_before = content[max(0, start_pos-200):start_pos]
        
        if '"""' in context_before or "'''" in context_before:
            return original
        
        # Generiere dummsicheren Kommentar
        comment = f'''
    # -------------------------------------------------------------------------
    # FUNKTION: {func_name}
    # PARAMETER: {params if params else 'keine'}
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    '''
        return original + comment
    
    content = re.sub(func_pattern, replace_func, content)
    return content

def enhance_class_comments(content):
    """Verbessert Kommentare für Klassen."""
    
    class_pattern = r'class (\w+)(\([^)]*\))?:'
    
    def replace_class(match):
        class_name = match.group(1)
        parents = match.group(2) or ''
        
        original = match.group(0)
        
        # Prüfen ob schon Kommentar da ist
        start_pos = match.start()
        context_before = content[max(0, start_pos-200):start_pos]
        
        if '"""' in context_before or "'''" in context_before:
            return original
        
        comment = f'''
    # ========================================================================
    # KLASSE: {class_name}{parents}
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    '''
        return original + comment
    
    content = re.sub(class_pattern, replace_class, content)
    return content

def process_file(filepath):
    """Verarbeitet eine einzelne Datei."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        original_content = content
        
        # Header hinzufügen
        content = add_header_comments(filepath, content)
        
        # Funktionen kommentieren
        content = enhance_function_comments(content)
        
        # Klassen kommentieren  
        content = enhance_class_comments(content)
        
        # Nur schreiben wenn sich was geändert hat
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
        
    except Exception as e:
        print(f'Fehler bei {filepath}: {e}')
        return False

# Hauptprogramm
if __name__ == '__main__':
    processed = 0
    skipped = 0
    
    for root, dirs, files in os.walk('.'):
        # Überspringen von .git, __pycache__, etc.
        if '.git' in root or '__pycache__' in root or 'egg-info' in root or 'tests/' in root:
            continue
            
        for f in files:
            if f.endswith('.py'):
                filepath = os.path.join(root, f)
                if process_file(filepath):
                    processed += 1
                    print(f'Kommentiert: {filepath}')
                else:
                    skipped += 1
    
    print(f'\nFertig! {processed} Dateien verbessert, {skipped} unverändert.')
