
import traceback

import sys
import logging

import numpy as np
import os
import time
import subprocess
import socket
import json
import random

import torch
from contextlib import closing

from carla.tcp import TCPConnectionError
from carla.driving_benchmark import run_driving_benchmark

from drive import CoILAgent
from logger import coil_logger


from logger import monitorer


from configs import g_conf, merge_with_yaml, set_type_of_process

from utils.checkpoint_schedule import  maximun_checkpoint_reach, get_next_checkpoint,\
    is_next_checkpoint_ready, get_latest_evaluated_checkpoint
from utils.general import compute_average_std_separatetasks, get_latest_path, write_header_control_summary,\
    snakecase_to_camelcase, write_data_point_control_summary, camelcase_to_snakecase, unique


def frame2numpy(frame, frame_size):
    return np.resize(np.fromstring(frame, dtype='uint8'), (frame_size[1], frame_size[0], 3))



def find_free_port():

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


# TODO: The out part is only used for docker

def start_carla_simulator(gpu, town_name, no_screen, docker):

    # Set the outfiles for the process
    carla_out_file = os.path.join('_output_logs',
                      'CARLA_'+ g_conf.PROCESS_NAME + '_' + str(os.getpid()) + ".out")
    carla_out_file_err = os.path.join('_output_logs',
                      'CARLA_err_'+ g_conf.PROCESS_NAME + '_' + str(os.getpid()) + ".out")

    port = find_free_port()


    if docker:


        sp = subprocess.Popen(['docker', 'run', '--rm', '-d' ,'-p', str(port)+'-'+str(port+2)+':'+str(port)+'-'+str(port+2),
                              '--runtime=nvidia', '-e', 'NVIDIA_VISIBLE_DEVICES='+str(gpu), 'carlagear',
                               '/bin/bash', 'CarlaUE4.sh', '/Game/Maps/' + town_name,'-windowed',
                               '-benchmark', '-fps=10', '-world-port=' + str(port)], shell=False,
                              stdout=subprocess.PIPE)

        (out, err) = sp.communicate()


        

        #print (['docker', 'run', '--rm', '-p '+str(port)+'-'+str(port+2)+':'+str(port)+'-'+str(port+2),
        #                      '--runtime=nvidia', '-e  NVIDIA_VISIBLE_DEVICES='+str(gpu), 'carlasim/carla:0.8.4',
        #                       '/bin/bash', 'CarlaUE4.sh', '/Game/Maps/' + town_name,'-windowed',
        #                       '-benchmark', '-fps=10', '-world-port=' + str(port)])


    else:

        carla_path = os.environ['CARLA_PATH']
        if not no_screen:
            os.environ['SDL_HINT_CUDA_DEVICE'] = str(gpu)
            sp = subprocess.Popen([carla_path + '/CarlaUE4/Binaries/Linux/CarlaUE4', '/Game/Maps/' + town_name,
                                    '-windowed',
                                   '-benchmark', '-fps=10', '-world-port='+str(port)], shell=False,
                                   stdout=open(carla_out_file, 'w'), stderr=open(carla_out_file_err, 'w'))

        else:
            os.environ['DISPLAY'] =":5"
            sp = subprocess.Popen(['vglrun', '-d', ':7.' + str(gpu),
                                        carla_path + '/CarlaUE4/Binaries/Linux/CarlaUE4',
                                        '/Game/Maps/' + town_name, '-windowed', '-benchmark',
                                        '-fps=10', '-world-port='+str(port)],
                                   shell=False,
                                   stdout=open(carla_out_file, 'w'), stderr=open(carla_out_file_err, 'w'))
        out = "0"



    coil_logger.add_message('Loading', {'CARLA':  '/CarlaUE4/Binaries/Linux/CarlaUE4' 
                           '-windowed'+ '-benchmark'+ '-fps=10'+ '-world-port='+ str(port)})

    return sp, port, out




# OBS: note, for now carla and carla test are in the same GPU

# TODO: Add all the necessary logging.

# OBS : I AM FIXING host as localhost now
# TODO :  Memory use should also be adaptable with a limit, for now that seems to be doing fine in PYtorch


def execute(gpu, exp_batch, exp_alias, drive_conditions, params):


            #, host='127.0.0.1',
            #suppress_output=True, no_screen=False, docker=False):


    try:


        print("Running ", __file__, " On GPU ", gpu, "of experiment name ", exp_alias)
        os.environ["CUDA_VISIBLE_DEVICES"] = gpu

        if not os.path.exists('_output_logs'):
            os.mkdir('_output_logs')

        merge_with_yaml(os.path.join('configs', exp_batch, exp_alias + '.yaml'))


        print ("drive cond", drive_conditions)
        exp_set_name, town_name = drive_conditions.split('_')

        if g_conf.USE_ORACLE:
            control_filename = 'control_output_auto'
        else:
            control_filename = 'control_output'


        experiment_suite_module = __import__('drive.suites.' + camelcase_to_snakecase(exp_set_name) + '_suite',
                                             fromlist=[exp_set_name])

        experiment_suite_module = getattr(experiment_suite_module, exp_set_name)




        experiment_set = experiment_suite_module()

        set_type_of_process('drive', drive_conditions)


        if params['suppress_output']:
            sys.stdout = open(os.path.join('_output_logs',
                              g_conf.PROCESS_NAME + '_' + str(os.getpid()) + ".out"),
                              "a", buffering=1)
            sys.stderr = open(os.path.join('_output_logs',
                              exp_alias + '_err_'+g_conf.PROCESS_NAME + '_' + str(os.getpid()) + ".out"),
                              "a", buffering=1)




        coil_logger.add_message('Loading', {'Poses': experiment_set.build_experiments()[0].poses})


        experiment_list = experiment_set.build_experiments()
        # Get all the uniquely named tasks
        task_list = unique([experiment.task_name for experiment in experiment_list ])
        # Now actually run the driving_benchmark

        print (" CARLA IS OPEN")
        latest = get_latest_evaluated_checkpoint(control_filename + '_' + task_list[0])


        if latest is None:  # When nothing was tested, get latest returns none, we fix that.
            latest = 0
            # The used tasks are hardcoded, this need to be improved
            file_base = os.path.join('_logs', exp_batch, exp_alias,
                         g_conf.PROCESS_NAME + '_csv', control_filename)
            #write_header_control_summary(file_base, 'empty')

            #write_header_control_summary(file_base, 'normal')
            print (g_conf.PROCESS_NAME)
            print (file_base)

            for i in range(len(task_list)):
                write_header_control_summary(file_base, task_list[i])



        # Write the header of the summary file used conclusion
        # While the checkpoint is not there
        while not maximun_checkpoint_reach(latest, g_conf.TEST_SCHEDULE):

            try:
                # Get the correct checkpoint
                # We check it for some task name, all of then are ready at the same time
                if is_next_checkpoint_ready(g_conf.TEST_SCHEDULE, control_filename + '_' + task_list[0]):


                    carla_process, port, out = start_carla_simulator(gpu, town_name,
                                                                     params['no_screen'], params['docker'])

                    latest = get_next_checkpoint(g_conf.TEST_SCHEDULE, control_filename + '_' + task_list[0])
                    checkpoint = torch.load(os.path.join('_logs', exp_batch, exp_alias
                                                         , 'checkpoints', str(latest) + '.pth'))


                    coil_agent = CoILAgent(checkpoint, town_name, params['record_collisions'])


                    coil_logger.add_message('Iterating', {"Checkpoint": latest}, latest)

                    run_driving_benchmark(coil_agent, experiment_set, town_name,
                                          exp_batch + '_' + exp_alias + '_' + str(latest)
                                          + '_drive_' + control_filename
                                          , True, params['host'], port)

                    path = exp_batch + '_' + exp_alias + '_' + str(latest) \
                           + '_' + g_conf.PROCESS_NAME.split('_')[0] + '_' + control_filename \
                           + '_' + g_conf.PROCESS_NAME.split('_')[1] + '_' + g_conf.PROCESS_NAME.split('_')[2]


                    print(path)
                    print("Finished")
                    benchmark_json_path = os.path.join(get_latest_path(path), 'metrics.json')
                    with open(benchmark_json_path, 'r') as f:
                        benchmark_dict = json.loads(f.read())

                    print (" number of episodes ", len(experiment_set.build_experiments()))
                    averaged_dict = compute_average_std_separatetasks([benchmark_dict],
                                                        experiment_set.weathers,
                                                        len(experiment_set.build_experiments()))

                    file_base = os.path.join('_logs', exp_batch, exp_alias,
                                             g_conf.PROCESS_NAME + '_csv', control_filename)
                    # TODO: Number of tasks is hardcoded
                    # TODO: Number of tasks is hardcoded

                    print ("TASK LIST ")
                    print (task_list)

                    for i in range(len(task_list)):
                        #write_data_point_control_summary(file_base, 'empty', averaged_dict, latest, 0)
                        #write_data_point_control_summary(file_base, 'normal', averaged_dict, latest, 1)
                        write_data_point_control_summary(file_base, task_list[i], averaged_dict, latest, i)

                    #plot_episodes_tracks(os.path.join(get_latest_path(path), 'measurements.json'),
                    #                     )
                    print (averaged_dict)


                    carla_process.kill()
                    subprocess.call(['docker', 'stop', out[:-1]])

                else:
                    time.sleep(0.1)




            except TCPConnectionError as error:
                logging.error(error)
                time.sleep(1)
                carla_process.kill()
                subprocess.call(['docker', 'stop', out[:-1]])
                coil_logger.add_message('Error', {'Message': 'TCP serious Error'})
                exit(1)
            except KeyboardInterrupt:
                carla_process.kill()
                subprocess.call(['docker', 'stop', out[:-1]])
                coil_logger.add_message('Error', {'Message': 'Killed By User'})
                exit(1)
            except:
                traceback.print_exc()
                carla_process.kill()
                subprocess.call(['docker', 'stop', out[:-1]])
                coil_logger.add_message('Error', {'Message': 'Something Happened'})
                exit(1)


        coil_logger.add_message('Finished', {})

    except KeyboardInterrupt:
        traceback.print_exc()
        coil_logger.add_message('Error', {'Message': 'Killed By User'})

    except:
        traceback.print_exc()
        coil_logger.add_message('Error', {'Message': 'Something happened'})



