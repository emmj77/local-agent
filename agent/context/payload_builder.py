import os
from typing import List, Dict
from pydantic import BaseModel, Field

# --- MODÈLE DE VALIDATION DU PAYLOAD SORTANT ---

class PayloadModel(BaseModel):
    system_prompt: str = Field(..., description="Le prompt système combinant SOUL et USER")
    current_input: str = Field(..., description="L'instruction utilisateur actuelle à traiter")
    conversation_history: List[Dict[str, str]] = Field(..., description="L'historique de session validé")

# --- CORE LOGIC ---

def assemble_payload(context_data: dict, user_input: str = "") -> dict:
    """
    Fusionne de manière structurée les données de contexte nettoyées.
    Génère un prompt système propre et isole l'input actuel.

    Args:
        context_data: dict avec clés "soul", "user", "history"
        user_input: l'input utilisateur actuel (passé par agent_loop)
    """
    # Construction du prompt système global en assemblant les blocs maîtres
    system_chunks = []
    
    if context_data.get("soul"):
        system_chunks.append(f"=== REGLES SYSTEME & SOUL ===\n{context_data['soul']}")
        
    if context_data.get("user"):
        system_chunks.append(f"=== CONTEXTE ENVIRONNEMENT UTILISATEUR ===\n{context_data['user']}")
        
    # Assemblage propre avec double saut de ligne
    system_prompt = "\n\n".join(system_chunks) if system_chunks else "Tu es un agent autonome."
    
    # Extraction de l'input et de l'historique
    current_input = user_input or context_data.get("input", "")
    conversation_history = context_data.get("history", [])
    
    # Pydantic valide que le dictionnaire final de sortie est parfaitement structuré
    validated_payload = PayloadModel(
        system_prompt=system_prompt,
        current_input=current_input,
        conversation_history=conversation_history
    )
    
    # Retourne le dictionnaire de données validé et prêt pour l'inférence
    return validated_payload.model_dump()

if __name__ == "__main__":
    print("--- Test du Payload Builder initialisé ---")
    # Simulation de données d'entrée
    mock_context = {
        "soul": "Règles de l'agent.",
        "user": "Données machine.",
        "input": "Fais une action.",
        "history": [{"role": "user", "content": "Hello"}]
    }
    try:
        payload = assemble_payload(mock_context)
        print("Statut : Payload assemblé et validé par Pydantic avec succès.")
        print(f"Clés générées : {list(payload.keys())}")
    except (ValueError, TypeError) as e:
        print(f"Erreur d'assemblage : {e}")
