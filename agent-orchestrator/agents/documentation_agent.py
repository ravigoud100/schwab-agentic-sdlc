"""Evidence-only documentation: model selects catalog facts; no unverified prose."""
import json
from governance.release_evidence import facts


class DocumentationOutputError(ValueError):
    def __init__(self,error,prompt,raw):
        super().__init__(str(error));self.prompt=prompt;self.raw_response=str(raw)


def validate_selection(payload,catalog):
    if not isinstance(payload,dict) or set(payload)!=set(catalog):raise ValueError('Exactly the five documentation sections required')
    for section,items in catalog.items():
        ids=payload[section]
        if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or set(ids)!=set(items):
            raise ValueError(f'Unsupported or missing documentation claims in {section}')


def generate(model,manifest,feedback=None):
    catalog=facts(manifest)
    prompt=f'''Prepare evidence-only engineering documentation. Return ONLY JSON mapping each section
name to an ordered array of its fact IDs. Include every supplied fact exactly once in its section.
No additional claims, prose, Markdown, paths, or implementation inference are permitted.
Verified requirement/design/implementation/test artifact lineage:
{json.dumps(manifest)}
Authoritative evidence-derived fact catalog:
{json.dumps(catalog)}
Previous validation errors: {json.dumps(feedback or [])}
Return this exact JSON shape, with every ID required (ordering may change):
{json.dumps({section:list(items) for section,items in catalog.items()})}
'''
    response=model.invoke(prompt,stage='documentation')
    try:
        payload=json.loads(response.text)
        validate_selection(payload,catalog)
    except (ValueError,TypeError) as error:
        raise DocumentationOutputError(error,prompt,response.text) from error
    document='# Verified engineering documentation\n\n'
    for section,ids in payload.items():
        document+='## '+section.replace('_',' ').title()+'\n\n'
        document+='\n\n'.join(catalog[section][i] for i in ids)+'\n\n'
    return document.encode(),payload,prompt,response.text
