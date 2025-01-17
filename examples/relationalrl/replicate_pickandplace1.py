import torch
from torch.nn import Parameter
import gym

from rlkit.data_management.obs_dict_replay_buffer import ObsDictRelabelingBuffer
from rlkit.torch.her.her import HerTwinSAC
import rlkit.torch.pytorch_util as ptu

from rlkit.torch.data_management.normalizer import CompositeNormalizer
from rlkit.torch.optim.mpi_adam import MpiAdam
from rlkit.launchers.launcher_util import run_experiment
from rlkit.torch.relational.networks import (AttentiveGraphPooling, GraphPropagation,
                                             QValueReNN, ValueReNN, PolicyReNN)
import torch.nn.functional as F
from rlkit.torch.sac.policies import FlattenTanhGaussianPolicy

from rlkit.launchers.launcher_util import run_experiment_here

def experiment(variant):
    try:
        import fetch_block_construction
    except ImportError as e:
        print(e)

    env = gym.make(variant['env_id'])
    env.unwrapped.render_image_obs = False
    if variant['set_max_episode_steps']:
        env.env._max_episode_steps = variant['set_max_episode_steps']

    action_dim = env.action_space.low.size

    value_graphprop_kwargs = dict(
        graph_module_kwargs=dict(
            # num_heads=num_query_heads,
            # embedding_dim=embedding_dim,
            embedding_dim=64,
            num_heads=1,
        ),
        layer_norm=layer_norm,
        num_query_heads=num_query_heads,
        num_relational_blocks=num_relational_blocks,
        activation_fnx=F.leaky_relu,
        recurrent_graph=recurrent_graph
    )

    qvalue_graphprop_kwargs = dict(
        graph_module_kwargs=dict(
            num_heads=num_query_heads,
            embedding_dim=embedding_dim,
        ),
        layer_norm=layer_norm,
        num_query_heads=num_query_heads,
        num_relational_blocks=num_relational_blocks,
        activation_fnx=F.leaky_relu,
        recurrent_graph=recurrent_graph
    )

    v_gp = GraphPropagation(**value_graphprop_kwargs)

    q1_gp = GraphPropagation(**qvalue_graphprop_kwargs)

    q2_gp = GraphPropagation(**qvalue_graphprop_kwargs)

    policy_gp = GraphPropagation(**value_graphprop_kwargs)

    policy_readout = AttentiveGraphPooling(mlp_kwargs=None)

    qf1_readout = AttentiveGraphPooling(mlp_kwargs=dict(
        hidden_sizes=mlp_hidden_sizes,
        output_size=1,
        input_size=variant['pooling_heads']*embedding_dim,
        layer_norm=layer_norm,
    ),)
    qf2_readout = AttentiveGraphPooling(mlp_kwargs=dict(
        hidden_sizes=mlp_hidden_sizes,
        output_size=1,
        input_size=variant['pooling_heads']*embedding_dim,
        layer_norm=layer_norm,
    ),)
    vf_readout = AttentiveGraphPooling(mlp_kwargs=dict(
        hidden_sizes=mlp_hidden_sizes,
        output_size=1,
        input_size=variant['pooling_heads']*embedding_dim,
        layer_norm=layer_norm,
    ),)

    shared_normalizer = CompositeNormalizer(object_dim + shared_dim + goal_dim,
                                            action_dim,
                                            default_clip_range=5,
                                            reshape_blocks=True,
                                            fetch_kwargs=dict(
                                                lop_state_dim=3,
                                                object_dim=object_dim,
                                                goal_dim=goal_dim
                                            ))

    qf1 = QValueReNN(
        graph_propagation=q1_gp,
        readout=qf1_readout,
        input_module_kwargs=dict(
            normalizer=shared_normalizer,
            object_total_dim=object_dim+shared_dim+goal_dim+action_dim,
            embedding_dim=64,
            layer_norm=layer_norm
        ),
        composite_normalizer=shared_normalizer,
    )

    qf2 = QValueReNN(
        graph_propagation=q2_gp,
        readout=qf2_readout,
        input_module_kwargs=dict(
            normalizer=shared_normalizer,
            object_total_dim=object_dim + shared_dim + goal_dim + action_dim,
            embedding_dim=64,
            layer_norm=layer_norm
        ),
        composite_normalizer=shared_normalizer,
    )

    vf = ValueReNN(
        graph_propagation=v_gp,
        readout=vf_readout,
        input_module_kwargs=dict(
            normalizer=shared_normalizer,
            object_total_dim=object_dim + shared_dim + goal_dim,
            embedding_dim=64,
            layer_norm=layer_norm
        ),
        composite_normalizer=shared_normalizer,
    )

    observation_key = 'observation'
    desired_goal_key = 'desired_goal'
    achieved_goal_key = desired_goal_key.replace("desired", "achieved")

    replay_buffer = ObsDictRelabelingBuffer(
        env=env,
        observation_key=observation_key,
        desired_goal_key=desired_goal_key,
        achieved_goal_key=achieved_goal_key,
        **variant['replay_buffer_kwargs']
    )

    policy = PolicyReNN(
        graph_propagation=policy_gp,
        readout=policy_readout,
        out_size=action_dim,
        input_module_kwargs=dict(
            normalizer=shared_normalizer,
            object_total_dim=object_dim + shared_dim + goal_dim,
            embedding_dim=64,
            layer_norm=layer_norm
        ),
        num_relational_blocks=num_relational_blocks,
        num_query_heads=num_query_heads,
        mlp_class=FlattenTanhGaussianPolicy, # KEEP IN MIND
        mlp_kwargs=dict(
            hidden_sizes=mlp_hidden_sizes,
            obs_dim=variant['pooling_heads'] * embedding_dim,
            action_dim=action_dim,
            output_activation=torch.tanh,
            layer_norm=layer_norm,
            # init_w=3e-4,
        ),
        composite_normalizer=shared_normalizer
    )

    algorithm = HerTwinSAC(
        her_kwargs=dict(
            observation_key='observation',
            desired_goal_key='desired_goal',
            **variant['her_kwargs']
        ),
        tsac_kwargs=dict(
            env=env,
            qf1=qf1,
            qf2=qf2,
            vf=vf,
            policy=policy,
            optimizer_class=MpiAdam,
        ),
        replay_buffer=replay_buffer,
        **variant['algo_kwargs']
    )
    algorithm.to(ptu.device)
    algorithm.train()

if __name__ == "__main__":
    action_dim = 4
    object_dim = 15
    goal_dim = 3

    shared_dim = 10
    num_relational_blocks = 3
    num_query_heads = 1

    embedding_dim = 64
    layer_norm = True
    num_blocks = 1

    num_epochs_per_eval = 10

    max_path_len = 50
    max_episode_steps = 50
    # max_path_len = 50*num_blocks
    #
    # max_episode_steps = 50*num_blocks

    mlp_hidden_sizes=[64, 64, 64]
    stackonly = False # False means there is reward to move hand out of way

    mode = "local"
    gpu_mode = 'cpu'

    recurrent_graph=False

    variant = dict(
        algo_kwargs=dict(
            num_epochs=3000 * 10,
            max_path_length=max_path_len,
            batch_size=256,
            discount=0.98,
            save_algorithm=True,
            collection_mode='batch', # TODO: set these settings from now on
            num_updates_per_epoch=50*num_blocks,
            num_steps_per_epoch=50*num_blocks, # Do one episode per block
            num_steps_per_eval=50*num_blocks * 10, # Do ten episodes per eval
            num_epochs_per_eval=10, # One episode per epoch, so this is roughly 10 episodes per eval * number of parallel episodes...
            num_epochs_per_param_save=10 * 5, # TODO: set these settings for hypersweeps
            # min_num_steps_before_training=10000,

            #SAC args start
            soft_target_tau=0.001,
            policy_lr=3E-4,
            qf_lr=3E-4,
            vf_lr=3E-4,
            grad_clip_max=1000
        ),
        replay_buffer_kwargs=dict(
            max_size=int(1e5),
            fraction_goals_rollout_goals=0.2,  # equal to k = 4 in HER paper
            fraction_goals_env_goals=0.0,
            num_relational=num_relational_blocks,
            num_heads=num_query_heads
        ),
        render=False,
        env_id=F"FetchBlockConstruction_{num_blocks}Blocks_IncrementalReward_DictstateObs_42Rendersize_{stackonly}Stackonly_SingletowerCase-v1", # TODO: make sure FalseStackonly so it goes in the air
        save_video=False,
        save_video_period=50,
        num_relational_blocks=num_relational_blocks,
        set_max_episode_steps=max_episode_steps,
        mlp_hidden_sizes=mlp_hidden_sizes,
        num_query_heads=num_query_heads,
        action_dim=action_dim,
        goal_dim=goal_dim,
        embedding_dim=embedding_dim,
        pooling_heads=1,
        her_kwargs=dict(
            exploration_masking=True
        ),
        recurrent_graph=recurrent_graph
    )

    test_prefix = f"pickandplace1_seed1_recurrent{recurrent_graph}"
    print(f"\nprefix: {test_prefix}")

    exp_prefix = F"{test_prefix}_stack{num_blocks}_numrelblocks{num_relational_blocks}_nqh{num_query_heads}_{stackonly}stackonly_recurrent{recurrent_graph}"

    run_experiment_here(
        experiment,
        variant=variant,
        exp_id=0,
        seed=None,
        use_gpu=False,
        gpu_id=0,
        exp_prefix=exp_prefix,  # Make sure no spaces..
        snapshot_mode='gap_and_last',
        snapshot_gap=100,
        git_infos=None,
        script_name=None,
        base_log_dir=None,
        force_randomize_seed=False,
        log_dir=None,
    )
