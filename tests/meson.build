# Salkim modifications by Erhan Bilgili on:
# 2026-09-01, 2026-09-29, 2026-10-04 and 2026-10-10.

reflex_test_source = custom_target('reflex-lifetime-source',
  input : [ 'test_reflex_lifetime.py', '../libs/vkd3d/swapchain.c', '../include/private/vkd3d_threads.h' ],
  output : 'reflex_lifetime.c',
  command : [ find_program('python3', 'python', native : true), '@INPUT0@', '--generate', '@OUTPUT@' ])

reflex_test = executable('reflex-lifetime', reflex_test_source,
  dependencies : threads_dep,
  c_args : vkd3d_compiler.get_supported_arguments([ '-UNDEBUG', '-Wno-unused-function', '-Wno-unused-variable' ]),
  install : false)

foreach reflex_case : [ 'request-marker', 'request-timing', 'marker-lifetime', 'timing-lifetime',
                       'semaphore-readers', 'serialized-sleeps', 'overlapping-waits',
                       'absent', 'failed', 'out-of-band' ]
  test('reflex-' + reflex_case, reflex_test, args : [ reflex_case ], timeout : 10)
endforeach

# These tests record production command-list code without requiring a Vulkan device.
dgc_test_args = meson.get_compiler('c', native : true).get_supported_arguments([
  '-UNDEBUG', '-Wno-unused-function', '-Wno-unused-variable',
  '-Wno-unused-parameter', '-Wno-unused-but-set-variable' ])

foreach recorder : [ 'dgc_recording', 'dgc_replay', 'wbi_recording',
                     'root_descriptor_failures' ]
  recorder_inputs = [ 'test_' + recorder + '.py', '../libs/vkd3d/command.c' ]
  if recorder == 'root_descriptor_failures'
    recorder_inputs += [ 'test_dgc_recording.py' ]
  endif
  recorder_source = custom_target(recorder + '-source',
    input : recorder_inputs,
    output : recorder + '.c',
    command : [ find_program('python3', 'python', native : true), '@INPUT0@', '--generate', '@OUTPUT@' ])
  recorder_test = executable(recorder, recorder_source,
    native : true,
    include_directories : vkd3d_external_includes,
    c_args : dgc_test_args,
    install : false)
  if recorder == 'dgc_recording'
    foreach recording_case : [ 'capture-success', 'capture-failure', 'capture-eligibility',
                              'count-success', 'count-failure', 'count-absent', 'stream-failure',
                              'preprocess-success', 'preprocess-failure', 'preprocess-existing',
                              'root-ubo-success', 'root-ubo-failure' ]
      test('dgc-' + recording_case, recorder_test, args : [ recording_case ], timeout : 10)
    endforeach
  else
    test(recorder, recorder_test, timeout : 10)
  endif
endforeach

vkd3d_test_flags = []
vkd3d_test_deps = [ lib_d3d12, vkd3d_common_dep ]
if vkd3d_platform == 'windows'
  vkd3d_test_deps += [ lib_dxgi ]
endif

prebuild_test_source = custom_target('rtas-prebuild-source',
  input : [ 'rtas_prebuild_test.py', '../libs/vkd3d/device.c' ],
  output : 'rtas_prebuild.c',
  command : [ find_program('python3', 'python', native : true), '@INPUT0@', '--generate', '@OUTPUT@' ])

prebuild_test = executable('rtas-prebuild', prebuild_test_source,
  native : true,
  include_directories : vkd3d_external_includes,
  c_args : meson.get_compiler('c', native : true).get_supported_arguments([
    '-UNDEBUG', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter' ]),
  install : false)

test('rtas-prebuild', prebuild_test, timeout : 10)

d3d12_test_utils_lib = static_library('d3d12-test-utils', 'd3d12_test_utils.c',
  dependencies        : vkd3d_test_deps,
  include_directories : vkd3d_private_includes)

hud_test_source = custom_target('swapchain-hud-source',
  input : [ 'test_swapchain_hud.py', '../libs/vkd3d/swapchain.c' ],
  output : 'swapchain_hud.c',
  command : [ find_program('python3', 'python', native : true), '@INPUT0@', '--generate', '@OUTPUT@' ])

hud_test = executable('swapchain-hud', hud_test_source,
  native : true,
  include_directories : vkd3d_external_includes,
  c_args : meson.get_compiler('c', native : true).get_supported_arguments([
    '-UNDEBUG', '-Wno-unused-function', '-Wno-unused-variable',
    '-Wno-unused-parameter', '-Wno-unused-but-set-variable' ]),
  install : false)

foreach hud_case : [ 'empty', 'publication', 'barrier', 'direct', 'blank', 'failed', 'queued' ]
  test('swapchain-hud-' + hud_case, hud_test, args : [ hud_case ], timeout : 10)
endforeach

d3d12_test_src = [
  'd3d12_dgc_barriers.c',
  'd3d12_dgc_state.c',
  'd3d12_wbi_dgc.c',
  'd3d12_dxvk_interop_device.c',
  'd3d12_clip_cull_distance.c',
  'd3d12_enhanced_barriers.c',
  'd3d12_sampler_feedback.c',
  'd3d12_geometry_shader.c',
  'd3d12_win32_exclusive.c',
  'd3d12_root_signature.c',
  'd3d12_depth_stencil.c',
  'd3d12_render_target.c',
  'd3d12_tessellation.c',
  'd3d12_sm_advanced.c',
  'd3d12_mesh_shader.c',
  'd3d12_descriptors.c',
  'd3d12_raytracing.c',
  'd3d12_robustness.c',
  'd3d12_workgraphs.c',
  'd3d12_streamout.c',
  'd3d12_pso_blob.c',
  'd3d12_resource.c',
  'd3d12_bindless.c',
  'd3d12_dstorage.c',
  'd3d12_shaders.c',
  'd3d12_command.c',
  'd3d12_sparse.c',
  'd3d12_device.c',
  'd3d12_clear.c',
  'd3d12_query.c',
  'd3d12_sync.c',
  'd3d12_copy.c',
  'd3d12_vrs.c',
  'd3d12_pso.c',
  'd3d12_rov.c',
  'd3d12_va.c',
  'd3d12.c',
]

executable('d3d12', d3d12_test_src,
  dependencies        : vkd3d_test_deps,
  include_directories : vkd3d_private_includes,
  install             : false,
  c_args              : vkd3d_test_flags,
  link_with           : [ d3d12_test_utils_lib ])

executable('descriptor-performance', 'descriptor_performance.c',
  dependencies        : vkd3d_test_deps,
  include_directories : vkd3d_private_includes,
  install             : false,
  c_args              : vkd3d_test_flags,
  link_with           : [ d3d12_test_utils_lib ])

executable('pso-library-bloat', 'pso_library_bloat.c',
  dependencies        : vkd3d_test_deps,
  include_directories : vkd3d_private_includes,
  install             : false,
  c_args              : vkd3d_test_flags,
  link_with           : [ d3d12_test_utils_lib ])
