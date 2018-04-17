from flask import Flask, request, send_file, render_template
from werkzeug import secure_filename
import simplejson
import datetime
from data_access import *
import luigi_runner, luigi_pipeline
from flask_autodoc import Autodoc
from nlp import *
from upload import upload_file, upload_from_db, aact_db_upload
from ohdsi import *
import json

app = Flask(__name__)
auto = Autodoc(app)

init_status = "none"


def init():
    global init_status
    if init_status == "none" or init_status == "error":
        try:
            init_status = "loading"
            section_tagger_init()
            segmentation_init()
            context_init()
            init_status = "done"
        except Exception as ex:
            print(ex)
            init_status = "error"


@app.route('/')
def home():
    return "Welcome to Clarity NLP"


@app.route('/documentation')
def doc():
    return auto.html()

@app.route('/ohdsi_patient_event', methods=['POST'])
@auto.doc()
def ohdsi_patient_event():
    """Get patient event details."""
    if request.method == 'POST':
        r = request.get_json()
        patient_event_details = getPatientEvent(r['cohort_id'], r['domain'], tuple(r['concepts']), util.conn_string_2)

        return patient_event_details

    return "Could not retrieve patient event details"

@app.route('/ohdsi_create_cohort', methods=['GET'])
@auto.doc()
def ohdsi_create_cohort():
    """Get concept set details."""
    if request.method == 'GET':
        filepath = 'ohdsi/data/' + request.args.get('file')
        cohort = createCohort(filepath)
        return cohort

    return "Could not retrieve Cohort"

@app.route('/ohdsi_get_conceptset', methods=['GET'])
@auto.doc()
def ohdsi_get_conceptset():
    """Get concept set details."""
    if request.method == 'GET':
        filepath = 'ohdsi/data/' + request.args.get('file')
        conceptset = getConceptSet2(filepath)
        return conceptset

    return "Could not retrieve Concept Set"

@app.route('/ohdsi_get_cohort', methods=['GET'])
@auto.doc()
def ohdsi_get_cohort():
    """Get cohort details from OHDSI."""
    if request.method == 'GET':
        cohort_id = request.args.get('cohort_id')
        cohort = getCohort(cohort_id)
        return cohort

    return "Could not retrieve Cohort"

@app.route('/ohdsi_get_cohort_by_name', methods=['GET'])
@auto.doc()
def ohdsi_get_cohort_by_name():
    """Get cohort details from OHDSI by giving Cohort name."""
    if request.method == 'GET':
        cohort_name = request.args.get('cohort_name')
        cohort = getCohortByName(cohort_name)
        return cohort

    return "Could not retrieve Cohort"


@app.route('/upload', methods=['POST', 'GET'])
def upload():
    if request.method == 'GET':
        return render_template('solr_upload.html')

    elif request.method == 'POST':
        f = request.files['file']
        filepath = os.path.join(util.tmp_dir, secure_filename(f.filename))
        f.save(filepath)
        msg = upload_file(util.solr_url, filepath)
        os.remove(filepath)
        return render_template('solr_upload.html', result=msg)

    return "ERROR. Contact Admin and try again later."


@app.route('/upload_from_db', methods=['GET'])
@auto.doc()
def db_to_solr():
    """Migrate data from DB to Solr."""
    if request.method == 'GET':
        msg = upload_from_db(util.conn_string2, util.solr_url)
        return msg

    return "Couldn't migrate data."


@app.route('/upload_from_aact', methods=['GET'])
@auto.doc()
def aact_upload():
    """Migrate data from aact DB to Solr."""
    if request.method == 'GET':
        msg = aact_db_upload(util.solr_url)
        return msg

    return "Couldn't migrate data."


@app.route('/phenotype', methods=['POST'])
@auto.doc()
def phenotype():
    """POST a phenotype job (JSON) to run"""
    if not request.data:
        return 'POST a JSON phenotype config to execute or an id to GET. Body should be phenotype JSON'
    try:
        init()
        p_cfg = PhenotypeModel.from_dict(request.get_json())
        p_id = insert_phenotype_model(p_cfg, util.conn_string)
        if p_id == -1:
            return '{ "success", false }'
        job_id = jobs.create_new_job(jobs.NlpJob(job_id=-1, name=p_cfg.description, description=p_cfg.description,
                                                 owner=p_cfg.owner, status=jobs.STARTED, date_ended=None,
                                                 phenotype_id=p_id, pipeline_id=-1,
                                                 date_started=datetime.datetime.now(),
                                                 job_type='PHENOTYPE'), util.conn_string)

        pipeline_ids = luigi_runner.run_phenotype(p_cfg, p_id, job_id)

        output = dict()
        output["phenotype_id"] = str(p_id)
        output["job_id"] = str(job_id)
        output['pipelines'] = pipeline_ids
        output["status_endpoint"] = "%s/status?job=%s" % (util.main_url, str(job_id))
        output["main_results_endpoint"] = "%s/job_results?job=%s&type=%s" % (util.main_url, str(job_id), 'phenotype')
        output["pipeline_results_endpoint"] = "%s/job_results?job=%s&type=%s" % (util.main_url, str(job_id), 'pipeline')
        output["luigi_task_monitoring"] = "%s/static/visualiser/index.html#search__search=job=%s" % (util.luigi_url, str(job_id))

        return json.dumps(output, indent=4)

    except Exception as ex:
        return 'Failed to load and insert phenotype. ' + str(ex), 400


@app.route('/pipeline', methods=['POST'])
@auto.doc()
def pipeline():
    """POST a pipeline job (JSON) to run on the Luigi pipeline."""
    if not request.data:
        return 'POST a JSON pipeline config to execute or an id to GET. Body should be pipeline JSON'
    try:
        init()
        p_cfg = PipelineConfig.from_dict(request.get_json())
        p_id = insert_pipeline_config(p_cfg, util.conn_string)
        if p_id == -1:
            return '{ "success", false }'
        job_id = jobs.create_new_job(jobs.NlpJob(job_id=-1, name=p_cfg.name, description=p_cfg.description,
                                                 owner=p_cfg.owner, status=jobs.STARTED, date_ended=None,
                                                 phenotype_id=-1, pipeline_id=p_id,
                                                 date_started=datetime.datetime.now(),
                                                 job_type='PIPELINE'), util.conn_string)

        luigi_runner.run_pipeline(p_cfg.config_type, str(p_id), job_id, p_cfg.owner)

        output = dict()
        output["pipeline_id"] = str(p_id)
        output["job_id"] = str(job_id)
        output["status_endpoint"] = "%s/status?job=%s" % (util.main_url, str(job_id))
        output["results_endpoint"] = "%s/job_results?job=%s&type=%s" % (util.main_url, str(job_id), 'pipeline')
        output["luigi_task_monitoring"] = "%s/static/visualiser/index.html#search__search=job=%s" % (
        util.luigi_url, str(job_id))

        return json.dumps(output, indent=4)

    except Exception as e:
        return 'Failed to load and insert pipeline. ' + str(e), 400


@app.route('/pipeline_id', methods=['GET'])
@auto.doc()
def pipeline_id():
    """GET a pipeline JSON based on the pipeline_id, PARAMETERS: id=pipeline id"""
    try:
        pid = request.args.get('id')
        return json.dumps(get_pipeline_config(pid, util.conn_string), indent=4)
    except Exception as e:
        return "Failed to extract pipeline id parameter" + str(e)


@app.route('/pipeline_types', methods=['GET'])
@auto.doc()
def pipeline_types():
    """GET valid pipeline types"""
    try:
        return repr(list(luigi_pipeline.luigi_pipeline_types.keys()))
    except Exception as e:
        return "Failed to get pipeline types" + str(e)


@app.route('/status', methods=['GET'])
@auto.doc()
def get_job_status():
    """GET current job status, PARAMETERS: job=job id"""
    try:
        job = request.args.get('job')
        status = jobs.get_job_status(int(job), util.conn_string)
        return json.dumps(status, indent=4)
    except Exception as e:
        return "Failed to get job status" + str(e)


@app.route('/job_results', methods=['GET'])
def get_job_results():
    """GET job results as CSV, PARAMETERS: job=job id, type=job type"""
    try:
        job = request.args.get('job')
        job_type = request.args.get('type')
        job_output = job_results(job_type, job)
        return send_file(job_output)
    except Exception as ex:
        return "Failed to get job results" + str(ex)


@app.route('/sections', methods=['GET'])
@auto.doc()
def get_section_source():
    """GET source file for sections and synonyms"""
    try:
        file_path = get_sec_tag_source_tags()
        return send_file(file_path)
    except Exception as ex:
        print(ex)
        return "Failed to retrieve sections source file"


@app.route('/ngram', methods=['GET'])
@auto.doc()
def get_ngram():
    """GET n-grams for a cohort, PARAMETERS: cohort_id=cohort_id, keyword=keyword, n=ngram length, frequency=cutoff frequency"""
    if request.method == 'GET':
        cohort_id = request.args.get('cohort_id')
        keyword = request.args.get('keyword')
        n = request.args.get('n')
        frequency = request.args.get('frequency')

        print(cohort_id)
        print(keyword)
        print(n)
        print(frequency)

        result = extract_ngrams(cohort_id, keyword, int(n), int(frequency))

        ans = '\n'.join(result)

        return ans
    return 'Unable to extract n-gram'


@app.route('/measurement_finder', methods=['POST'])
@auto.doc()
def measurement_finder():
    """POST to extract measurements, See samples/sample_measurement_finder.json"""
    if request.method == 'POST' and request.data:
        init()
        obj = NLPModel.from_dict(request.get_json())

        results = run_measurement_finder_full(obj.text, obj.terms)
        return json.dumps([r.__dict__ for r in results], indent=4)
    return "Please POST a valid JSON object with terms and text"


@app.route('/term_finder', methods=['POST'])
@auto.doc()
def term_finder():
    """POST to extract terms, context, negex, sections from text, See samples/sample_term_finder.json"""
    if request.method == 'POST' and request.data:
        init()
        obj = NLPModel.from_dict(request.get_json())
        finder = TermFinder(obj.terms)

        results = finder.get_term_full_text_matches(obj.text)
        return json.dumps([r.__dict__ for r in results], indent=4)
    return "Please POST a valid JSON object with terms and text"


@app.route('/value_extractor', methods=['POST'])
@auto.doc()
def value_extractor():
    """POST to extract values such as BP, LVEF, Vital Signs etc. (See samples/sample_value_extractor.json)"""
    if request.method == 'POST' and request.data:
        init()
        obj = NLPModel.from_dict(request.get_json())
        results = run_value_extractor_full(obj.terms, obj.text, obj.min_value, obj.max_value, obj.case_sensitive)

        return json.dumps([r.__dict__ for r in results], indent=4)
    return "Please POST a valid JSON object with terms and text"


@app.route('/named_entity_recognition', methods=['POST'])
@auto.doc()
def named_entity_recognition():
    """POST to extract standard named entities. (See samples/sample_ner.json)"""
    if request.method == 'POST' and request.data:
        init()
        obj = NLPModel.from_dict(request.get_json())
        results = get_standard_entities(obj.text)

        return json.dumps([r.__dict__ for r in results], indent=4)
    return "Please POST a valid JSON object with text"


@app.route('/pos_tagger', methods=['POST'])
@auto.doc()
def pos_tagger():
    """POST to extract Tags. (See samples/sample_pos_tag_text.json)"""
    if request.method == 'POST' and request.data:
        init()
        obj = NLPModel.from_dict(request.get_json())
        tags = get_tags(obj.text)

        return json.dumps([t.__dict__ for t in tags], indent=4)
    return "Please POST a valid JSON object with text"


@app.route("/tnm_stage", methods=["POST"])
@auto.doc()
def tnm_stage():
    """POST to extract TNM cancer stage (See samples/sample_tnm_stage.json)"""
    if request.method == 'POST' and request.data:
        init()
        obj = NLPModel.from_dict(request.get_json())
        res = run_tnm_stager_full(obj.text)

        return json.dumps(res, indent=4)
    return "Please POST a valid JSON object text"


@app.route("/report_type_mappings", methods=["GET"])
@auto.doc()
def report_type_mappings():
    """GET dictionary of report type mappings"""
    mappings = get_report_type_mappings(util.report_mapper_url, util.report_mapper_inst, util.report_mapper_key)
    return simplejson.dumps(mappings, sort_keys=True, indent=4 * ' ')


@app.route('/vocab_expansion', methods=['GET'])
@auto.doc()
def vocabulary_expansion():
    """GET related terms based a user entered term, PARAMETERS: type=1 (synonyms), 2 (ancestors), 3 (descendants), concept=user entered term, vocab=(optional, default is SNOMED)"""
    if request.method == 'GET':

        k = request.args.get('type')
        concept = request.args.get('concept')
        vocab = request.args.get('vocab')
        print(vocab)

        result = {"vocab": []}

        if k == '1':
            r = get_synonyms(util.conn_string, concept, vocab)
        elif k == '2':
            r = get_ancestors(util.conn_string, concept, vocab)
        elif k == '3':
            r = get_descendants(util.conn_string, concept, vocab)
        else:
            return 'Incorrect request format'

        for i in r:
            result['vocab'].append(i[0])

        return str(result)

    return 'Vocabulary Expansion Failed'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
