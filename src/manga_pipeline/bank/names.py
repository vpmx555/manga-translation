"""Metadata-only, idempotent name updates; never touch embeddings or crops."""
import copy
from pathlib import Path

from ..storage.io import atomic_json, read_json
from ..storage.locking import exclusive_lock


class NameBank:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.is_file():
            raise FileNotFoundError(f"Character bank missing: {self.path}")
        return read_json(self.path)

    def known(self):
        return known_names(self.read())

    def bind(self, operation, character_id, name, evidence, *, source):
        """v3 source priority; stable identity evidence precedes alias changes."""
        priorities = {"direct_address": 1, "introduction": 2}
        if source not in priorities:
            raise ValueError("Name binding requires introduction or direct-address evidence")
        with exclusive_lock(self.path.parent / ".bank.lock"):
            bank = self.read()
            operations = bank.setdefault("name_operations", {})
            if operation in operations:
                return copy.deepcopy(operations[operation])
            character = bank.get("characters", {}).get(str(character_id))
            if type(character_id) is not int or not character or character.get("disabled"):
                raise ValueError("Name target is not an enabled stable character ID")
            name = name.strip()
            if not name:
                raise ValueError("Empty name")
            mapped = known_names(bank).get(name.casefold(), [])
            if mapped and set(mapped) != {character_id}:
                raise ValueError("Name already belongs to another stable ID")
            old = character.get("display_name")
            manual = bool(old) and (character.get("name_source") != "model"
                                    or old != character.get("last_auto_name"))
            priority = priorities[source]
            old_priority = character.get("name_priority", 1 if old and not manual else 0)
            aliases = character.setdefault("aliases", [])

            def alias(value):
                if value and value.casefold() not in {x.casefold() for x in aliases}:
                    aliases.append(value)

            promoted, conflict = False, False
            if not old or (not manual and priority > old_priority):
                promoted = bool(old and old.casefold() != name.casefold())
                if promoted:
                    alias(old)
                character["aliases"] = [x for x in aliases if x.casefold() != name.casefold()]
                character.update(display_name=name, name_source="model", last_auto_name=name,
                                 name_priority=priority, name_evidence_source=source)
            elif old.casefold() == name.casefold():
                if not manual and priority >= old_priority:
                    character.update(name_priority=priority, name_evidence_source=source)
            else:
                alias(name)
                conflict = not manual and priority == old_priority == 2
            if manual:
                character["name_source"] = "manual"
            result = {"status": "protected" if manual else "linked", "character_id": character_id,
                      "name": name, "previous_name": old, "current_name": character.get("display_name"),
                      "alias": bool(old and character.get("display_name", "").casefold() != name.casefold()),
                      "promoted": promoted, "name_conflict": conflict, "source": source}
            character.setdefault("name_history", []).append({"operation": operation, "name": name,
                "old_name": old, "source": source, "evidence": evidence,
                "promoted": promoted, "conflict": conflict})
            operations[operation] = result
            atomic_json(self.path, bank)
            return copy.deepcopy(result)

    def apply(self, operation, character_id, name, evidence, *, preserve_name=False):
        with exclusive_lock(self.path.parent / ".bank.lock"):
            bank = self.read()
            operations = bank.setdefault("name_operations", {})
            if operation in operations:
                return copy.deepcopy(operations[operation])
            character = bank["characters"].get(str(character_id))
            if character is None or character.get("disabled"):
                raise ValueError("Name target is not an enabled stable character ID")
            name = name.strip()
            if not name:
                raise ValueError("Empty name")
            if preserve_name:
                mapped = known_names(bank).get(name.casefold(), [])
                if mapped and set(mapped) != {character_id}:
                    raise ValueError("Name already belongs to another stable ID")
            old = character.get("display_name")
            manually_changed = bool(old) and (
                character.get("name_source") != "model"
                or old != character.get("last_auto_name"))
            if preserve_name and old:
                aliases = character.setdefault("aliases", [])
                if name.casefold() != old.casefold() and name.casefold() not in {x.casefold() for x in aliases}:
                    aliases.append(name)
                character.setdefault("name_history", []).append({
                    "operation": operation, "name": name, "evidence": evidence, "alias": True})
                result = {"status": "linked", "character_id": character_id, "name": name, "alias": True}
            elif manually_changed:
                character["name_source"] = "manual"
                result = {"status": "protected", "character_id": character_id,
                          "name": name, "current_name": old}
            else:
                character.update(display_name=name, name_source="model", last_auto_name=name)
                character.setdefault("name_history", []).append({
                    "operation": operation, "old_name": old, "name": name, "evidence": evidence})
                result = {"status": "linked", "character_id": character_id, "name": name}
            operations[operation] = result
            # Mapping and operation receipt are one atomic transaction. A crash
            # before committing a target cannot apply this operation twice.
            atomic_json(self.path, bank)
            return result


def known_names(bank):
    result = {}
    for key, character in bank.get("characters", {}).items():
        if character.get("disabled"):
            continue
        for name in [character.get("display_name"), *character.get("aliases", [])]:
            if name:
                identities = result.setdefault(name.casefold().strip(), [])
                identity = character.get("id", int(key))
                if identity not in identities:
                    identities.append(identity)
    return result
