﻿// Copyright (C) 2016 iNuron NV
//
// This file is part of Open vStorage Open Source Edition (OSE),
// as available from
//
//      http://www.openvstorage.org and
//      http://www.openvstorage.com.
//
// This file is free software; you can redistribute it and/or modify it
// under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
// as published by the Free Software Foundation, in version 3 as it comes
// in the LICENSE.txt file of the Open vStorage OSE distribution.
//
// Open vStorage is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY of any kind.
/*global requirejs, define, window */
requirejs.config({
    paths: {
        'text'       : '../lib/require/text',
        'durandal'   : '../lib/durandal/js',
        'plugins'    : '../lib/durandal/js/plugins',
        'transitions': '../lib/durandal/js/transitions',
        'knockout'   : '../lib/knockout/knockout-3.3.0',
        'bootstrap'  : '../lib/bootstrap/js/bootstrap',
        'jquery'     : '../lib/jquery/jquery-1.9.1',
        'jqp'        : '../lib/jquery-plugins/js',
        'd3'         : '../lib/d3/d3.v3.min',
        'd3p'        : '../lib/d3-plugins/js',
        'ovs'        : '../lib/ovs',
        'i18next'    : '../lib/i18next/i18next.amd.withJQuery-1.7.1'
    },
    shim: {
        'bootstrap': {
            deps   : ['jquery'],
            exports: 'jQuery'
        },
        'jqp/pnotify': {
            deps   : ['jquery'],
            exports: 'jQuery'
        },
        'jqp/timeago': {
            deps   : ['jquery'],
            exports: 'jQuery'
        },
        'd3': {
            exports: 'd3'
        },
        'd3p/slider': {
            deps   : ['d3'],
            exports: 'd3'
        }
    },
    urlArgs: 'version=0.0.0b0',
    waitSeconds: 300
});

define([
    'durandal/system', 'durandal/app', 'durandal/viewLocator', 'durandal/binder', 'jquery', 'i18next',
    'ovs/shared',
    'ovs/extensions/knockout-helpers', 'ovs/extensions/knockout-bindinghandlers', 'ovs/extensions/knockout-extensions', 'ovs/extensions/knockout-extenders',
    'bootstrap'
],  function(system, app, viewLocator, binder, $, i18n, shared) {
    "use strict";
    system.debug(true);

    shared.defaultLanguage = shared.language = window.navigator.userLanguage || window.navigator.language || 'en-US';
    var i18nOptions = {
        detectFromHeaders: false,
        lng: shared.defaultLanguage,
        fallbackLng: 'en-US',
        ns: 'ovs',
        resGetPath: requirejs.toUrl('/locales/__lng__/__ns__.json'),
        useCookie: false,
        useLocalStorage: false
    };

    if (window.localStorage.hasOwnProperty('nodes') && window.localStorage.nodes !== null) {
        shared.nodes = $.parseJSON(window.localStorage.nodes);
    }

    i18n.init(i18nOptions, function() {
        app.title = $.t('ovs:title');
        app.configurePlugins({
            router: true,
            dialog: true,
            widget: true
        });
        app.configurePlugins({
            widget: {
                kinds: ['pager', 'lazyloader', 'lazylist', 'footer', 'dropdown', 'accessrights']
            }
        });
        app.start().then(function() {
            viewLocator.useConvention();
            binder.binding = function(obj, view) {
                $(view).i18n();
            };
            app.setRoot('viewmodels/shell');
        });
    });
});
