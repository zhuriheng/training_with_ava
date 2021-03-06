from __future__ import print_function
import argparse
import pprint
import mxnet as mx

from ..config import config, default, generate_config
from ..symbol import *
from ..dataset import *
from ..core.loader import TestLoader
from ..core.tester import Predictor, pred_eval_fpn
from ..utils.load_model import load_param
import numpy as np


def test_rcnn(network, dataset, image_set, root_path, dataset_path,
              ctx, prefix, epoch,
              vis, shuffle, has_rpn, proposal, thresh,
              use_global_context, use_roi_align, use_box_voting, detailed_analysis):
    # set config
    if has_rpn:
        config.TEST.HAS_RPN = True

    # print config
    pprint.pprint(config)

    # load symbol and testing data
    if has_rpn:
        if use_global_context or use_roi_align:
            sym = eval('get_' + network + '_test')(num_classes=config.NUM_CLASSES, num_anchors=config.NUM_ANCHORS,
                                                   use_global_context=use_global_context, use_roi_align=use_roi_align)
        else:
            sym = eval('get_' + network + '_test')(num_classes=config.NUM_CLASSES, num_anchors=config.NUM_ANCHORS)
        imdb = eval(dataset)(image_set, root_path, dataset_path)
        roidb = imdb.gt_roidb()
    else:
        if use_global_context or use_roi_align:
            sym = eval('get_' + network + '_rcnn_test')(num_classes=config.NUM_CLASSES,
                                                        use_global_context=use_global_context, use_roi_align=use_roi_align)
        else:
            sym = eval('get_' + network + '_rcnn_test')(num_classes=config.NUM_CLASSES)

        imdb = eval(dataset)(image_set, root_path, dataset_path)
        gt_roidb = imdb.gt_roidb()
        roidb = eval('imdb.' + proposal + '_roidb')(gt_roidb)

    # get test data iter
    test_data = TestLoader(roidb, batch_size=1, shuffle=shuffle, has_rpn=has_rpn)

    # load model
    arg_params, aux_params = load_param(prefix, epoch, convert=True, ctx=ctx, process=True)
    if use_global_context:
        # additional params for using global context
        for arg_param_name in sym.list_arguments():
            if 'stage5' in arg_param_name:
                # print(arg_param_name, arg_param_name.replace('stage5', 'stage4'))
                arg_params[arg_param_name] = arg_params[arg_param_name.replace('stage5', 'stage4')].copy()  # params of stage5 is initialized from stage4
        arg_params['bn2_gamma'] = arg_params['bn1_gamma'].copy()
        arg_params['bn2_beta'] = arg_params['bn1_beta'].copy()

        for aux_param_name in sym.list_auxiliary_states():
            if 'stage5' in aux_param_name:
                # print(aux_param_name, aux_param_name.replace('stage5', 'stage4'))
                aux_params[aux_param_name] = aux_params[aux_param_name.replace('stage5', 'stage4')].copy()  # params of stage5 is initialized from stage4
        aux_params['bn2_moving_mean'] = aux_params['bn1_moving_mean'].copy()
        aux_params['bn2_moving_var'] = aux_params['bn1_moving_var'].copy()

    # infer shape
    data_shape_dict = dict(test_data.provide_data)
    arg_shape, _, aux_shape = sym.infer_shape(**data_shape_dict)
    arg_shape_dict = dict(zip(sym.list_arguments(), arg_shape))
    aux_shape_dict = dict(zip(sym.list_auxiliary_states(), aux_shape))

    # check parameters
    for k in sym.list_arguments():
        if k in data_shape_dict or 'label' in k:
            continue
        assert k in arg_params, k + ' not initialized'
        assert arg_params[k].shape == arg_shape_dict[k], \
            'shape inconsistent for ' + k + ' inferred ' + str(arg_shape_dict[k]) + ' provided ' + str(arg_params[k].shape)
    for k in sym.list_auxiliary_states():
        assert k in aux_params, k + ' not initialized'
        assert aux_params[k].shape == aux_shape_dict[k], \
            'shape inconsistent for ' + k + ' inferred ' + str(aux_shape_dict[k]) + ' provided ' + str(aux_params[k].shape)

    # decide maximum shape
    data_names = [k[0] for k in test_data.provide_data]
    label_names = None
    max_data_shape = [('data', (1, 3, max([v[0] for v in config.SCALES]), max([v[1] for v in config.SCALES])))]
    if not has_rpn:
        max_data_shape.append(('rois', (1, config.TEST.PROPOSAL_POST_NMS_TOP_N + 30, 5)))

    # create predictor
    predictor = Predictor(sym, data_names, label_names,
                          context=ctx, max_data_shapes=max_data_shape,
                          provide_data=test_data.provide_data, provide_label=test_data.provide_label,
                          arg_params=arg_params, aux_params=aux_params)

    # start detection
    pred_eval_fpn(predictor, test_data, imdb, vis=vis, thresh=thresh, use_box_voting=use_box_voting, detailed_analysis=detailed_analysis)
    if dataset == "imagenet":
         return np.mean(imdb.ap)


def parse_args():
    parser = argparse.ArgumentParser(description='Test a Fast R-CNN network')
    # general
    parser.add_argument('--network', help='network name', default=default.network, type=str)
    parser.add_argument('--dataset', help='dataset name', default=default.dataset, type=str)
    args, rest = parser.parse_known_args()
    generate_config(args.network, args.dataset)
    parser.add_argument('--image_set', help='image_set name', default=default.test_image_set, type=str)
    parser.add_argument('--root_path', help='output data folder', default=default.root_path, type=str)
    parser.add_argument('--dataset_path', help='dataset path', default=default.dataset_path, type=str)
    # testing
    parser.add_argument('--prefix', help='model to test with', default=default.rcnn_prefix, type=str)
    parser.add_argument('--epoch', help='model to test with', default=default.rcnn_epoch, type=int)
    parser.add_argument('--gpu', help='GPU device to test with', default=0, type=int)
    # rcnn
    parser.add_argument('--vis', help='turn on visualization', action='store_true')
    parser.add_argument('--thresh', help='valid detection threshold', default=1e-3, type=float)
    parser.add_argument('--shuffle', help='shuffle data on visualization', action='store_true')
    parser.add_argument('--has_rpn', help='generate proposals on the fly', action='store_true')
    parser.add_argument('--proposal', help='can be ss for selective search or rpn', default='rpn', type=str)
    # tricks
    parser.add_argument('--use_global_context', help='use roi global context for classification', action='store_true')
    parser.add_argument('--use_roi_align', help='replace ROIPooling with ROIAlign', action='store_true')
    parser.add_argument('--use_box_voting', help='use box voting in test', action='store_true')
    # analysis
    parser.add_argument('--detailed_analysis', help='give detailed analysis result, e.g. APs in different scale ranges',
                        action='store_true')

    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    ctx = mx.gpu(args.gpu)
    print(args)
    test_rcnn(args.network, args.dataset, args.image_set, args.root_path, args.dataset_path,
              ctx, args.prefix, args.epoch,
              args.vis, args.shuffle, args.has_rpn, args.proposal, args.thresh, args.use_global_context,
              args.use_roi_align, args.use_box_voting, args.detailed_analysis)

if __name__ == '__main__':
    main()
