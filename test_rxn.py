import sys
from rdkit import Chem
from rdkit.Chem import rdChemReactions
from rdkit.Chem import rdMolDescriptors

rxn_content = """$RXN
      -OEChem-02262614532D

  1  1
$MOL

  1  0  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$MOL

  2  0  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.0000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
M  END
"""

rxn = rdChemReactions.ReactionFromRxnBlock(rxn_content)
print(f"Reactants: {rxn.GetNumReactantTemplates()}")
print(f"Products: {rxn.GetNumProductTemplates()}")

for i in range(rxn.GetNumReactantTemplates()):
    mol = rxn.GetReactantTemplate(i)
    frags = Chem.GetMolFrags(mol, asMols=True)
    print(f"Reactant {i} fragments: {len(frags)}")

for i in range(rxn.GetNumProductTemplates()):
    mol = rxn.GetProductTemplate(i)
    frags = Chem.GetMolFrags(mol, asMols=True)
    print(f"Product {i} fragments: {len(frags)}")
    for f in frags:
        print("  Frag formula:", rdMolDescriptors.CalcMolFormula(f))
