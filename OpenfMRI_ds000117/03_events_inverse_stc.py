"""
============================
04. Compute inverse solution
============================
The inverse solution pipeline performs source reconstruction starting from the
raw data specified by the user. The input raw data are epoched accordingly to
events specified in json file and created in script 03. The evoked datasets are
created by averaging the different conditions specified in json file.
"""

import os.path as op
import json
import pprint  # noqa

import nipype.pipeline.engine as pe
from nipype.interfaces.utility import Function

from ephypype.nodes import create_iterator, create_datagrabber
from ephypype.pipelines.fif_to_inv_sol import create_pipeline_source_reconstruction  # noqa


# Relative path
rel_path = op.split(op.realpath(__file__))[0]
print(rel_path)

# Read experiment params as json
params = json.load(open(op.join(rel_path, "params.json")))
pprint.pprint({'parameters': params})

data_type = params["data_type"]
subject_ids = params["subject_ids"]
NJOBS = params["NJOBS"]
session_ids = params["session_ids"]

if "data_path" in params.keys():
    data_path = op.join(params["data_path"], "data_demo")
else:
    data_path = op.join(op.expanduser("~"), "data_demo")
print(data_path)

conditions = params["conditions"]
subjects_dir = op.join(data_path, params["subjects_dir"])

# Read the parameters for inverse solution from a json file and print it
data = json.load(open(op.join(rel_path, "params_inverse.json")))
pprint.pprint({'inverse parameters': data})

events_id = data['events_id']
condition = data['condition']
events_file = data['events_file']
t_min = data['tmin']
t_max = data['tmax']
spacing = data['spacing']  # oct-6
snr = data['snr']
inv_method = data['method']  # dSPM
parc = data['parcellation']  # aparc
trans_fname = data['trans_fname']


###############################################################################
# Function to extract events from the stimulus channel
###############################################################################
def run_events_concatenate(list_ica_files, subject):
    '''
    The events are extracted from stim channel 'STI101'. The events are saved
    to the Node directory.
    For each subject, the different run are concatenated in one single raw file
    and saved in the Node directory. We take the different run from the
    preprocessing workflow directory, i.e. the cleaned raw data.
    '''

    print(subject, list_ica_files)
    import os
    import mne

    # could be added in a node to come
    mask = 4096 + 256  # mask for excluding high order bits
    delay_item = 0.0345
    min_duration = 0.015

    print("processing subject: %s" % subject)

    raw_list = list()
    events_list = list()
    fname_events_files = []

    print("  Loading raw data")
    for i, run_fname in enumerate(list_ica_files):
        run = i+1

        raw = mne.io.read_raw_fif(run_fname, preload=True)
        events = mne.find_events(raw, stim_channel='STI101',
                                 consecutive='increasing', mask=mask,
                                 mask_type='not_and',
                                 min_duration=min_duration)

        print("  S %s - R %s" % (subject, run))

        fname_events = os.path.abspath('run_%02d-eve.fif' % run)
        mne.write_events(fname_events, events)
        fname_events_files.append(fname_events)

        delay = int(round(delay_item * raw.info['sfreq']))
        events[:, 0] = events[:, 0] + delay
        events_list.append(events)

        raw_list.append(raw)

    raw, events = mne.concatenate_raws(raw_list, events_list=events_list)
    raw.set_eeg_reference(projection=True)
    raw_file = os.path.abspath('{}_sss_filt_ica-raw.fif'.format(subject))
    print(raw_file)

    raw.save(raw_file, overwrite=True)

    event_file = os.path.abspath('{}_sss_filt_ica-raw-eve.fif'.format(subject))
    mne.write_events(event_file, events)

    del raw_list
    del raw

    return raw_file, event_file, fname_events_files


###############################################################################
# Group average on source level
###############################################################################
def compute_morph_stc(subject, conditions, cond_files, subjects_dir):
    import os.path as op
    import mne

    print("processing subject: %s" % subject)

    # Morph STCs
    stc_morphed_files = []
    for k, cond_file in enumerate(cond_files):
        print(conditions[k])
        print(cond_file)
        stc = mne.read_source_estimate(cond_file)

        morph = mne.compute_source_morph(
                        stc, subject_from=subject, subject_to='fsaverage',
                        subjects_dir=subjects_dir)
        stc_morphed = morph.apply(stc)
        stc_morphed_file = op.abspath('mne_dSPM_inverse_morph-%s' % (conditions[k]))  # noqa
        stc_morphed.save(stc_morphed_file)

        stc_morphed_files.append(stc_morphed_file)

    return stc_morphed_files


def show_files(files):
    print(files)
    return files


###############################################################################
# Defining pipeline to compute inverse solution
###############################################################################
# Then, we create our workflow and specify the `base_dir` which tells
# nipype the directory in which to store the outputs.

# workflow directory within the `base_dir`
src_reconstruction_pipeline_name = 'source_dsamp_full_reconstruction_' + \
    inv_method + '_' + parc.replace('.', '')

main_workflow = pe.Workflow(name=src_reconstruction_pipeline_name)
main_workflow.base_dir = data_path

# We create a node to pass input filenames to DataGrabber from nipype
infosource = create_iterator(['subject_id'], [subject_ids])

# and a node to grab data. The template_args in this node iterate upon
# the values in the infosource node
ica_dir = op.join(
        data_path, 'preprocessing_dsamp_workflow', 'preproc_meeg_dsamp_pipeline')  # noqa

template_path = "_session_id_*_subject_id_%s/ica/run_*_sss_filt_dsamp_ica.fif"
template_args = [['subject_id']]
infields = ['subject_id']
datasource = create_datagrabber(ica_dir, template_path, template_args,
                                infields=infields)

# We connect the output of infosource node to the one of datasource.
# So, these two nodes taken together can grab data.
main_workflow.connect(infosource, 'subject_id', datasource,  'subject_id')

# We define the Node that encapsulates run_events_concatenate function
concat_event = pe.Node(
    Function(input_names=['list_ica_files', 'subject'],
             output_names=['raw_file', 'event_file', 'fname_events_files'],
             function=run_events_concatenate),
    name='concat_event')

# and its connections to the other nodes
main_workflow.connect(datasource, ('raw_file', show_files),
                      concat_event, 'list_ica_files')
main_workflow.connect(infosource, 'subject_id', concat_event, 'subject')

###############################################################################
# Ephypype creates for us a pipeline to compute inverse solution which can be
# connected to these nodes we created.
inv_sol_workflow = create_pipeline_source_reconstruction(
    data_path, subjects_dir, spacing=spacing, inv_method=inv_method,
    is_epoched=True, is_evoked=True, events_id=events_id, condition=condition,
    t_min=t_min, t_max=t_max,
    trans_fname=trans_fname, all_src_space=True, parc=parc)

main_workflow.connect(infosource, ('subject_id', show_files),
                      inv_sol_workflow, 'inputnode.sbj_id')
main_workflow.connect(concat_event, ('raw_file', show_files),
                      inv_sol_workflow, 'inputnode.raw')
main_workflow.connect(concat_event, ('event_file', show_files),
                      inv_sol_workflow, 'inputnode.events_file')


# We define the Node that encapsulates compute_morph_stc function
morph_stc = pe.Node(
    Function(input_names=['subject', 'conditions', 'cond_files', 'subjects_dir'],  # noqa
             output_names=['stc_morphed_files'],
             function=compute_morph_stc),
    name="morph_stc")

# and its connections to the other nodes
main_workflow.connect(infosource, 'subject_id', morph_stc, 'subject')
main_workflow.connect(inv_sol_workflow, 'inv_solution.stc_files',
                      morph_stc, 'cond_files')

morph_stc.inputs.conditions = conditions
morph_stc.inputs.subjects_dir = subjects_dir

###############################################################################
# To do so, we first write the workflow graph (optional)
main_workflow.write_graph(graph2use='colored')  # colored

# Finally, we are now ready to execute our workflow.
main_workflow.config['execution'] = {'remove_unnecessary_outputs': 'false'}
# Run workflow locally on 1 CPU
main_workflow.run(plugin='LegacyMultiProc', plugin_args={'n_procs': NJOBS})