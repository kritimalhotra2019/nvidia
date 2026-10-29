"""Combine already registered datasets with explicit schema and provenance checks."""
import pandas as pd
from ..registry import tool, InvalidArguments
from .datasets import read_dataset

@tool('combine_datasets', 'Append datasets with matching columns, or join on explicit keys. Choose how=inner for a matched cohort, left/right to retain that source, or outer to retain all keys. Reports matched and unmatched source-row counts. The default left join requires every left row to match. Originals are preserved; linkage does not establish causality.',
 {'type':'object','properties':{
  'dataset_ids':{'type':'array','items':{'type':'string'},'minItems':2,'maxItems':10},
  'mode':{'type':'string','enum':['append','join']},
  'keys':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':10},
  'relationship':{'type':'string','enum':['one_to_one','many_to_one','one_to_many']},
  'how':{'type':'string','enum':['inner','left','right','outer']},
  'require_all_left':{'type':'boolean','description':'For left joins, reject unmatched left rows by default. Set false explicitly to retain them with missing right values.'},
  'title':{'type':'string','minLength':1,'maxLength':100}},
 'required':['dataset_ids','mode'],'additionalProperties':False})
def combine_datasets(ctx,args):
    ids=args['dataset_ids']
    if len(set(ids))!=len(ids):raise InvalidArguments('Select distinct datasets; do not append a file to itself')
    datasets=[ctx.project.dataset(i) for i in ids]
    frames=[]
    for ds in datasets:
        ctx.project.allow.check_readable(ds.path)
        frames.append(read_dataset(ds));ctx.check()
    provenance=[{'dataset_id':d.id,'name':d.name,'rows':len(f)} for d,f in zip(datasets,frames)]
    audit=None
    if args['mode']=='append':
        if any(k in args for k in ('keys','relationship','how','require_all_left')):raise InvalidArguments('Keys, relationship and join options apply only to joins')
        columns=list(frames[0].columns)
        if any(set(f.columns)!=set(columns) for f in frames[1:]):
            raise InvalidArguments('Append schemas differ. Harmonize column names and meanings before combining; no columns were silently discarded.')
        if '_source_dataset_id' in columns:raise InvalidArguments('Reserved provenance column already exists')
        frames=[f.assign(_source_dataset_id=d.id) for d,f in zip(datasets,frames)]
        combined=pd.concat(frames,ignore_index=True)
    else:
        if len(frames)!=2 or not args.get('keys'):raise InvalidArguments('Join needs exactly two datasets and explicit keys')
        keys=args['keys']
        if any(any(k not in f.columns for k in keys) for f in frames):raise InvalidArguments('Join key missing from a source')
        if any(f[keys].isna().any().any() for f in frames):raise InvalidArguments('Join keys contain missing values; resolve them first')
        if any('_merge' in f.columns for f in frames):raise InvalidArguments('Reserved _merge column exists; rename it explicitly before joining')
        how=args.get('how','left')
        if 'require_all_left' in args and how!='left':raise InvalidArguments('require_all_left applies only to a left join')
        try:
            combined=frames[0].merge(frames[1],on=keys,how=how,validate=args.get('relationship','one_to_one'),indicator=True,suffixes=('_left','_right'))
            counts=[]
            for left,right in [(frames[0],frames[1]),(frames[1],frames[0])]:
                matched=left[keys].merge(right[keys].drop_duplicates(),on=keys,how='left',indicator=True)
                counts.append(int((matched['_merge']=='both').sum()))
        except (pd.errors.MergeError,ValueError) as e:raise InvalidArguments('Join keys/cardinality failed: '+str(e)+'. Check key types and duplicate keys; no output was committed.') from e
        audit={'how':how,'keys':keys,'relationship':args.get('relationship','one_to_one'),
               'left_rows':len(frames[0]),'right_rows':len(frames[1]),
               'matched_left_rows':counts[0],'matched_right_rows':counts[1],
               'unmatched_left_rows':len(frames[0])-counts[0],'unmatched_right_rows':len(frames[1])-counts[1],
               'output_rows':len(combined)}
        if how=='left' and args.get('require_all_left',True) and audit['unmatched_left_rows']:
            raise InvalidArguments(f"{audit['unmatched_left_rows']} left rows have no right match. No output was committed. For an explicitly requested matched cohort use how='inner'; to retain unmatched left rows use how='left', require_all_left=false. Report exclusions.")
        combined=combined.drop(columns=['_merge'])
    ctx.check();out=ctx.project.new_artifact_dir('combine',args.get('title','Combined survey'));path=out/'combined.csv'
    combined.to_csv(path,index=False)
    ds=ctx.project.register_dataset(path,'csv',None,len(combined),len(combined.columns),list(combined.columns))
    ctx.project.record_dataset_sources(ds.id,ids)
    provenance_path=out/'provenance.json'
    ctx.project.write_json(provenance_path,{'mode':args['mode'],'sources':provenance,'output_rows':len(combined),'output_dataset':ds.id,'join_audit':audit})
    return {'dataset_id':ds.id,'path':str(path),'rows':len(combined),'columns':list(combined.columns),'source_datasets':provenance,'combination_mode':args['mode'],
     'join_audit':audit,'provenance_path':str(provenance_path),
     'summary_text':f'Combined {len(ids)} sources into {len(combined)} rows by {args["mode"]}. Originals unchanged; provenance saved.'}
