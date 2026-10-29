"""No model/network calls: prediction identity, privacy, validation and saved outputs."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from squipi_worker import tools  # noqa: F401
from squipi_worker.objectives import digest
from squipi_worker.project import Project
from squipi_worker.registry import InvalidArguments, JobContext, registry
from squipi_worker.security import AllowList
from squipi_worker.tools import prediction


def base_frame():
    rows = 22
    targets = [None if i in (3, 17) else ('returned' if i % 2 else 'not_returned') for i in range(rows)]
    return pd.DataFrame({'entity': ['entity-' + str(i) for i in range(rows)],
                         'age': np.arange(rows) + 25, 'cohort': ['A' if i % 3 else 'B' for i in range(rows)],
                         'outcome': targets})


def arguments(**changes):
    args = {'dataset_id': 'base', 'id_column': 'entity', 'target_column': 'outcome',
            'feature_columns': ['age', 'cohort'], 'task': 'classification',
            'data_mode': 'cross_sectional'}
    return {**args, **changes}


def linked_frame(base):
    return pd.DataFrame({'entity_key': base['entity'].repeat(2).to_numpy(),
                         'attendance': np.tile([3.0, 7.0], len(base))})


def linked_spec():
    return {'dataset_id': 'linked', 'foreign_key': 'entity_key', 'feature_columns': ['attendance']}


def test_query_order_and_class_mapping_survive_interspersed_labels():
    source = base_frame(); before = source.copy(deep=True)
    p, query = prediction.prepare_prediction(source, arguments(), [])
    assert query == [3, 17]
    assert p['n_context'] == 20 and p['n_query'] == 2
    assert p['entity_keys'][-2:] == [3, 17]
    assert p['features'][-2:] == [{'age': 28, 'cohort': 'B'}, {'age': 42, 'cohort': 'A'}]
    assert p['classes'] == ['not_returned', 'returned']
    assert [p['classes'][i] for i in p['targets']] == source.loc[source.outcome.notna(), 'outcome'].tolist()
    assert p['engine'] == 'tabular'
    assert all('entity' not in row and 'outcome' not in row for row in p['features'])
    pd.testing.assert_frame_equal(source, before)


def test_integer_and_string_class_labels_keep_distinct_mapping():
    source = base_frame()
    known = source.outcome.notna()
    source.loc[known, 'outcome'] = pd.Series([1] * 10 + ['1'] * 10, index=source.index[known], dtype=object)
    p, _ = prediction.prepare_prediction(source, arguments(), [])
    assert p['classes'] == [1, '1']
    assert p['targets'] == [0] * 10 + [1] * 10


@pytest.mark.parametrize('mode', [None, 'temporal', ''])
def test_cross_sectional_design_must_be_explicit(mode):
    args = arguments(data_mode=mode)
    with pytest.raises(InvalidArguments, match='cross_sectional'):
        prediction.prepare_prediction(base_frame(), args, [])


@pytest.mark.parametrize('case', ['repeated_id', 'missing_id', 'target_feature', 'copy_target',
                                  'unknown_category', 'missing_feature', 'infinite_feature',
                                  'unlabelled_all', 'labelled_all', 'single_class', 'tiny_class',
                                  'too_many_classes', 'temporal_feature'])
def test_invalid_base_inputs_fail_before_inference(case):
    source = base_frame(); args = arguments()
    if case == 'repeated_id': source.loc[1, 'entity'] = source.loc[0, 'entity']
    elif case == 'missing_id': source.loc[1, 'entity'] = None
    elif case == 'target_feature': args['feature_columns'] = ['age', 'outcome']
    elif case == 'copy_target':
        source['copied'] = source['outcome'].fillna('returned'); args['feature_columns'] = ['age', 'copied']
    elif case == 'unknown_category': source.loc[3, 'cohort'] = 'unseen'
    elif case == 'missing_feature': source.loc[1, 'age'] = np.nan
    elif case == 'infinite_feature':
        source['age'] = source['age'].astype(float); source.loc[1, 'age'] = np.inf
    elif case == 'unlabelled_all': source['outcome'] = None
    elif case == 'labelled_all': source['outcome'] = source.outcome.fillna('returned')
    elif case == 'single_class': source.loc[source.outcome.notna(), 'outcome'] = 'one'
    elif case == 'tiny_class':
        source.loc[source.outcome.notna(), 'outcome'] = 'majority'; source.loc[0, 'outcome'] = 'minority'
    elif case == 'too_many_classes':
        source.loc[source.outcome.notna(), 'outcome'] = ['c' + str(i % 11) for i in range(20)]
    elif case == 'temporal_feature':
        source['visit_date'] = '2026-01-01'; args['feature_columns'] = ['age', 'visit_date']
    with pytest.raises(InvalidArguments):
        prediction.prepare_prediction(source, args, [])


@pytest.mark.parametrize('name,value', [('email', 'person@example.com'), ('respondent_name', 'Jane Doe'),
                                      ('interview_notes', 'private notes'), ('device_id', 123),
                                      ('phone', '12345678901'), ('detail', 'https://example.com/private')])
def test_pii_identifier_and_free_text_features_are_refused(name, value):
    source = base_frame(); source[name] = value
    with pytest.raises(InvalidArguments, match='identifier|PII|free.text'):
        prediction.prepare_prediction(source, arguments(feature_columns=['age', name]), [])


def test_valid_star_schema_uses_relational_and_internal_keys():
    source = base_frame(); linked = linked_frame(source)
    p, _ = prediction.prepare_prediction(source, arguments(), [(linked_spec(), linked)])
    assert p['engine'] == 'relational'
    assert p['linked'][0]['entity_keys'] == [i for i in range(22) for _ in range(2)]
    assert p['linked'][0]['records'][0] == {'attendance': 3.0}
    assert 'entity_key' not in p['linked'][0]['records'][0]


@pytest.mark.parametrize('case', ['unknown_fk', 'missing_fk', 'target_leak', 'forced_tabular', 'forced_relational'])
def test_invalid_linkage_and_engine_combinations_fail(case):
    source = base_frame(); linked = linked_frame(source); spec = linked_spec(); args = arguments()
    links = [(spec, linked)]
    if case == 'unknown_fk': linked.loc[0, 'entity_key'] = 'not-in-base'
    elif case == 'missing_fk': linked.loc[0, 'entity_key'] = None
    elif case == 'target_leak':
        linked['outcome'] = 'returned'; spec['feature_columns'] = ['attendance', 'outcome']
    elif case == 'forced_tabular': args['engine'] = 'tabular'
    elif case == 'forced_relational': args['engine'] = 'relational'; links = []
    with pytest.raises(InvalidArguments):
        prediction.prepare_prediction(source, args, links)


def test_regression_targets_preserve_values_without_imputation():
    source = base_frame(); known = source.outcome.notna()
    source.loc[known, 'outcome'] = [3 + i * i for i in range(20)]
    p, query = prediction.prepare_prediction(source, arguments(task='regression'), [])
    assert p['targets'] == [float(3 + i * i) for i in range(20)]
    assert p['classes'] == [] and query == [3, 17]


@pytest.mark.parametrize('bad', ['not a number', float('inf'), 'constant'])
def test_invalid_regression_targets_fail(bad):
    source = base_frame(); known = source.outcome.notna()
    source.loc[known, 'outcome'] = list(range(20))
    if bad == 'constant': source.loc[known, 'outcome'] = 7
    else: source.loc[0, 'outcome'] = bad
    with pytest.raises(InvalidArguments, match='Regression'):
        prediction.prepare_prediction(source, arguments(task='regression'), [])


@pytest.mark.parametrize('rows', [[], [{'entity_key': 17, 'probabilities': [.4, .6]}, {'entity_key': 3, 'probabilities': [.4, .6]}],
                                   [{'entity_key': 3, 'probabilities': [.9, .6]}, {'entity_key': 17, 'probabilities': [.4, .6]}],
                                   [{'entity_key': 3, 'probabilities': [float('nan'), .6]}, {'entity_key': 17, 'probabilities': [.4, .6]}]])
def test_model_output_identity_and_probabilities_are_verified(rows):
    p, _ = prediction.prepare_prediction(base_frame(), arguments(), [])
    with pytest.raises(InvalidArguments): prediction.validate_output({'rows': rows}, p)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(prediction, 'runtime_configuration', lambda: {'device': 'cpu'})
    project = Project(tmp_path / 'project', AllowList())
    ctx = JobContext(project, lambda *args: None, lambda: False)
    sources = []
    for name, frame in [('Base', base_frame()), ('Visits', linked_frame(base_frame()))]:
        path = tmp_path / (name + '.csv'); frame.to_csv(path, index=False)
        project.allow.allow(path)
        ds = registry.call('import_dataset', {'path': str(path)}, ctx)['dataset_id']
        sources.append((ds, path))
    return ctx, sources


def stub_model(ctx, cfg, prepared, out):
    rows = [{'entity_key': key, 'probabilities': [.2, .8] if i == 0 else [.9, .1]}
            for i, key in enumerate(prepared['entity_keys'][prepared['n_context']:])]
    return {'rows': rows, 'engine': prepared['engine'], 'device': 'cpu', 'package_version': 'stub-no-model'}, .01


def test_host_lease_cannot_be_granted_by_tool_argument(world, monkeypatch):
    ctx, sources = world
    def forbidden(*args): pytest.fail('model must not start without host lease')
    monkeypatch.setattr(prediction, '_run_model', forbidden)
    with pytest.raises(InvalidArguments, match='exclusive'):
        registry.call('predict_outcome', arguments(dataset_id=sources[0][0]), ctx)
    with pytest.raises(InvalidArguments):
        registry.call('predict_outcome', arguments(dataset_id=sources[0][0], exclusive_model='kumo'), ctx)


def test_saved_predictions_lineage_and_word_class_mapping(world, monkeypatch):
    ctx, sources = world; ctx.exclusive_model = 'kumo'
    monkeypatch.setattr(prediction, '_run_model', stub_model)
    base_id, base_path = sources[0]; linked_id, linked_path = sources[1]
    original = {p: p.read_bytes() for _, p in sources}
    args = arguments(dataset_id=base_id, linked_tables=[{**linked_spec(), 'dataset_id': linked_id}],
                     filename='Expected outcomes.csv')
    r = registry.call('predict_outcome', args, ctx)
    saved = pd.read_csv(r['csv_path'])
    assert saved['entity_id'].tolist() == ['entity-3', 'entity-17']
    assert saved['source_row_number'].tolist() == [4, 18]
    assert saved['predicted_class'].tolist() == ['returned', 'not_returned']
    assert saved['probability_0'].tolist() == [.2, .9]
    assert r['selected_source_ids'] == [base_id, linked_id]
    assert set(r['source_lineage']) == {base_id, linked_id}
    assert r['source_lineage'] == {i: digest(ctx.project.dataset(i).path) for i, _ in sources}
    assert {d.id for d in ctx.project.dataset_lineage(r['prediction_dataset_id'])} >= {base_id, linked_id}
    assert r['uncertainty']['calibrated'] is False
    mapping = json.loads((Path(r['csv_path']).parent / 'class-mapping.json').read_text())
    assert mapping == r['class_mapping']
    assert [v['label'] for v in mapping] == ['not_returned', 'returned']
    assert all(p.read_bytes() == content for p, content in original.items())
    brief = registry.call('write_research_results_brief', {'analysis_ids': [r['analysis_id']],
                          'title': 'Outcome prediction scope', 'filename': 'Outcome brief.docx'}, ctx)
    assert Path(brief['docx_path']).is_file()
    assert brief['verification']['all_saved_result_cells_match'] is True
    from docx import Document
    text = ' '.join(c.text for t in Document(brief['docx_path']).tables for row in t.rows for c in row.cells)
    assert 'not_returned' in text and 'returned' in text and 'probability_1' in text


def test_changed_source_is_not_presented_as_valid_prediction(world, monkeypatch):
    ctx, sources = world; ctx.exclusive_model = 'kumo'; base_id = sources[0][0]
    def change_source(ctx, cfg, prepared, out):
        response = stub_model(ctx, cfg, prepared, out)
        source_path = Path(ctx.project.dataset(base_id).path)
        source_path.write_bytes(source_path.read_bytes() + b'\n')
        return response
    monkeypatch.setattr(prediction, '_run_model', change_source)
    with pytest.raises(InvalidArguments, match='source changed'):
        registry.call('predict_outcome', arguments(dataset_id=base_id), ctx)
