import sys
from rdkit import Chem
from rdkit.Chem import rdChemReactions
from rdkit.Chem import rdMolDescriptors

# Create a reaction with 1 reactant template that contains multiple separated components 
# (e.g., from a user drawing two molecules without a '+' sign, which RDKit treats as a single template with disconnected fragments).
# And a product template with multiple separated components.

# Reactants: Aspirin and Water
# Products: Salicylic Acid and Acetic Acid
rxn_smarts = "[CC(=O)Oc1ccccc1C(=O)O].[H]O[H]>>[CC(=O)O].[O]c1ccccc1C(=O)O"
rxn = rdChemReactions.ReactionFromSmarts(rxn_smarts)

print(f"Reactants (Templates): {rxn.GetNumReactantTemplates()}")
print(f"Products (Templates): {rxn.GetNumProductTemplates()}")

# By default, ReactionFromSmarts separates dot-disconnected things into multiple templates.
# To test the desktop application behavior, let's create a single molecule with disconnected fragments.

m_reactants = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O.O") # Aspirin + Water as a single mol
m_products = Chem.MolFromSmiles("CC(=O)O.Oc1ccccc1C(=O)O")  # Acetic Acid + Salicylic Acid as a single mol

print("\n--- Testing Single Graph / Multi-Fragment Parsing ---")

frags_r = Chem.GetMolFrags(m_reactants, asMols=True)
print(f"Reactant Single Template Fragments: {len(frags_r)}")
for f in frags_r:
    print(f"  Formula: {rdMolDescriptors.CalcMolFormula(f)}")

frags_p = Chem.GetMolFrags(m_products, asMols=True)
print(f"Product Single Template Fragments: {len(frags_p)}")
for f in frags_p:
    print(f"  Formula: {rdMolDescriptors.CalcMolFormula(f)}")

