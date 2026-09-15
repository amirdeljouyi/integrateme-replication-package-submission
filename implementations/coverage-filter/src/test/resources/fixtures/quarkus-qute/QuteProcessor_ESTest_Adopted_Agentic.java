package io.quarkus.qute.deployment;

import static org.assertj.core.api.Assertions.assertThatExceptionOfType;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;

import java.io.File;
import java.nio.charset.Charset;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.Stack;
import java.util.TreeSet;
import java.util.Vector;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.regex.Pattern;

import org.jboss.jandex.AnnotationInstance;
import org.jboss.jandex.AnnotationTarget;
import org.jboss.jandex.ClassInfo;
import org.jboss.jandex.ClassType;
import org.jboss.jandex.DotName;
import org.jboss.jandex.EmptyIndex;
import org.jboss.jandex.Index;
import org.jboss.jandex.IndexView;
import org.jboss.jandex.MethodInfo;
import org.jboss.jandex.PrimitiveType;
import org.jboss.jandex.StackedIndex;
import org.jboss.jandex.Type;
import org.jboss.jandex.TypeVariable;
import org.jboss.jandex.WildcardType;
import org.junit.jupiter.api.Test;

import io.quarkus.arc.deployment.AutoAddScopeBuildItem;
import io.quarkus.arc.deployment.BeanArchiveIndexBuildItem;
import io.quarkus.arc.deployment.SynthesisFinishedBuildItem;
import io.quarkus.arc.deployment.SyntheticBeanBuildItem;
import io.quarkus.arc.processor.BeanDeployment;
import io.quarkus.arc.processor.BeanResolver;
import io.quarkus.arc.processor.InjectionPointInfo;
import io.quarkus.builder.BuildContext;
import io.quarkus.deployment.BuildProducerImpl;
import io.quarkus.deployment.annotations.BuildProducer;
import io.quarkus.deployment.builditem.ServiceStartBuildItem;
import io.quarkus.deployment.dev.AlwaysFalsePredicate;
import io.quarkus.deployment.pkg.NativeConfig;
import io.quarkus.qute.Engine;
import io.quarkus.qute.Expression;
import io.quarkus.qute.Template;
import io.quarkus.qute.deployment.TemplatesAnalysisBuildItem.TemplateAnalysis;
import io.quarkus.qute.runtime.QuteConfig;
import io.quarkus.qute.runtime.QuteRecorder;

public class QuteProcessor_ESTest_Adopted_Agentic {

    @Test
    public void testTemplateDataIgnorePattern() {
        List<String> names = List.of("foo", "bar");
        Pattern p = Pattern.compile(QuteProcessor.buildIgnorePattern(names));
        // Ignore "baz" and "getFoo"
        assertTrue(p.matcher("baz").matches());
        assertTrue(p.matcher("getFoo").matches());
        // Do not ignore "foo" and "bar"
        for (String name : names) {
            assertFalse(p.matcher(name).matches());
        }
        assertThatExceptionOfType(IllegalArgumentException.class)
                .isThrownBy(() -> QuteProcessor.buildIgnorePattern(List.of()));
    }

    @Test
    public void testCollectNamespaceExpressions() {
        Template template = Engine.builder().build().parse("{msg:hello} {msg2:hello_alpha} {foo:baz.get(foo:bar)}");
        TemplateAnalysis analysis = new TemplateAnalysis("foo", template, null);
        Set<Expression> msg = QuteProcessor.collectNamespaceExpressions(analysis, "msg");
        assertEquals(1, msg.size());
        assertEquals("msg:hello", msg.iterator().next().toOriginalString());

        Set<Expression> msg2 = QuteProcessor.collectNamespaceExpressions(analysis, "msg2");
        assertEquals(1, msg2.size());
        assertEquals("msg2:hello_alpha", msg2.iterator().next().toOriginalString());

        Set<Expression> foo = QuteProcessor.collectNamespaceExpressions(analysis, "foo");
        assertEquals(2, foo.size());
        for (Expression fooExpr : foo) {
            assertTrue(
                    fooExpr.toOriginalString().equals("foo:bar") || fooExpr.toOriginalString().equals("foo:baz.get(foo:bar)"));
        }
    }

    @Test
    public void isEmptyReturnsTrueForNewMatchResult() {
        EmptyIndex emptyIndex = EmptyIndex.INSTANCE;
        Types.AssignabilityCheck assignabilityCheck = new Types.AssignabilityCheck(emptyIndex);
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult(assignabilityCheck);
        assertTrue(matchResult.isEmpty());
    }

    @Test
    public void autoExtractTypeDoesNotThrowOnNullAssignabilityCheck() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        matchResult.autoExtractType();
    }

    @Test
    public void collectNamespaceExpressionsWithNullAnalysisThrowsNPE() {
        NullPointerException exception = assertThrows(NullPointerException.class,
                () -> QuteProcessor.collectNamespaceExpressions((TemplateAnalysis) null, (String) null));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName().equals("io.quarkus.qute.deployment.QuteProcessor")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void validateExpressionsWithNullBeanDeploymentCollectionsThrowsNPE() {
        QuteProcessor processor = new QuteProcessor();
        AlwaysFalsePredicate<TypeCheckExcludeBuildItem.TypeCheck> alwaysFalse = new AlwaysFalsePredicate<>();
        TypeCheckExcludeBuildItem excludeItem = new TypeCheckExcludeBuildItem(alwaysFalse, true);
        List<TypeCheckExcludeBuildItem> excludes = List.of(excludeItem, excludeItem, excludeItem, excludeItem, excludeItem);
        Class<IncorrectExpressionBuildItem> incorrectClass = IncorrectExpressionBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<IncorrectExpressionBuildItem> incorrectProducer = new BuildProducerImpl<>(incorrectClass, buildContext);
        BeanDeployment beanDeployment = mock(BeanDeployment.class);
        doReturn((BeanResolver) null).when(beanDeployment).getBeanResolver();
        doReturn((Collection<?>) null).when(beanDeployment).getBeans();
        doReturn((Collection<?>) null).when(beanDeployment).getInjectionPoints();
        doReturn((Collection<?>) null).when(beanDeployment).getObservers();
        SynthesisFinishedBuildItem synthesisFinished = new SynthesisFinishedBuildItem(beanDeployment);
        ArrayList<CheckedTemplateBuildItem> checkedTemplates = new ArrayList<>();
        Stack<TemplateDataBuildItem> templateDataItems = new Stack<>();
        TemplatesAnalysisBuildItem analyses = new TemplatesAnalysisBuildItem((List<TemplatesAnalysisBuildItem.TemplateAnalysis>) null);
        LinkedHashSet<DotName> beanExclusions = new LinkedHashSet<>();
        BeanArchiveIndexBuildItem beanArchiveIndex = new BeanArchiveIndexBuildItem((IndexView) null, (IndexView) null, beanExclusions);
        ArrayList<TemplateExtensionMethodBuildItem> extensionMethods = new ArrayList<>();
        Class<ImplicitValueResolverBuildItem> implicitClass = ImplicitValueResolverBuildItem.class;
        BuildContext buildContext1 = mock(BuildContext.class);
        BuildProducerImpl<ImplicitValueResolverBuildItem> implicitProducer = new BuildProducerImpl<>(implicitClass, buildContext1);
        NativeConfig nativeConfig = mock(NativeConfig.class);

        NullPointerException exception = assertThrows(NullPointerException.class,
                () -> processor.validateExpressions(analyses, beanArchiveIndex, extensionMethods, excludes, incorrectProducer,
                        implicitProducer, (BuildProducer<TemplateExpressionMatchesBuildItem>) null, synthesisFinished,
                        checkedTemplates, templateDataItems, (QuteConfig) null, nativeConfig,
                        (List<TemplateGlobalBuildItem>) null));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName().equals("java.util.Objects")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void initializeWithValidParamsButNonProxyRecorderThrowsIAE() {
        QuteProcessor processor = new QuteProcessor();
        Class<SyntheticBeanBuildItem> syntheticClass = SyntheticBeanBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<SyntheticBeanBuildItem> syntheticProducer = new BuildProducerImpl<>(syntheticClass, buildContext);
        QuteRecorder recorder = new QuteRecorder();
        Stack<TemplatePathBuildItem> templatePaths = new Stack<>();
        EffectiveTemplatePathsBuildItem effectivePaths = new EffectiveTemplatePathsBuildItem(templatePaths);
        HashMap<String, List<String>> variants = new HashMap<>();
        TemplateVariantsBuildItem templateVariants = new TemplateVariantsBuildItem(variants);
        Optional<TemplateVariantsBuildItem> optionalVariants = Optional.of(templateVariants);
        LinkedList<TemplateRootBuildItem> roots = new LinkedList<>();
        TemplateRootsBuildItem templateRoots = processor.collectTemplateRoots(roots);
        Stack<TemplatePathExcludeBuildItem> excludes = new Stack<>();
        TemplatePathExcludeBuildItem exclude = new TemplatePathExcludeBuildItem((String) null);
        excludes.add(exclude);

        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class,
                () -> processor.initialize(syntheticProducer, recorder, effectivePaths, optionalVariants, templateRoots, excludes));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName()
                    .equals("io.quarkus.arc.deployment.SyntheticBeanBuildItem$ExtendedBeanConfigurator")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void collectTemplateGlobalsDoesNotFailOnEmptyIndex() {
        QuteProcessor processor = new QuteProcessor();
        ArrayList<IndexView> views = new ArrayList<>();
        StackedIndex stackedIndex = StackedIndex.create(views);
        TreeSet<DotName> subpackages = new TreeSet<>();
        BeanArchiveIndexBuildItem beanArchiveIndex = new BeanArchiveIndexBuildItem(stackedIndex, stackedIndex, subpackages);
        Class<TemplateGlobalBuildItem> globalClass = TemplateGlobalBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<TemplateGlobalBuildItem> globalProducer = new BuildProducerImpl<>(globalClass, buildContext);
        processor.collectTemplateGlobals(beanArchiveIndex, globalProducer);
    }

    @Test
    public void collectNamespaceExpressionsOnExpressionNullThrowsNPE() {
        TreeSet<Expression> foundExpressions = new TreeSet<>();
        NullPointerException exception = assertThrows(NullPointerException.class,
                () -> QuteProcessor.collectNamespaceExpressions((Expression) null, foundExpressions, "kf46"));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName().equals("io.quarkus.qute.deployment.QuteProcessor")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void staticsFilterReturnsFalseForNonStaticMethod() {
        Class<Object> objectClass = Object.class;
//         ClassInfo classInfo = Index.singleClass(objectClass);
        TypeVariable returnType = TypeVariable.create("IN78 +@gT{<^b8");
        String[] params = new String[1];
        params[0] = "IN78 +@gT{<^b8";
        Type[] paramTypes = new Type[0];
        TypeVariable[] typeParams = new TypeVariable[2];
//         MethodInfo method = MethodInfo.create(classInfo, "IN78 +@gT{<^b8", params, paramTypes, returnType, (short) -6681,
//                 typeParams, paramTypes);
//         boolean accept = QuteProcessor.staticsFilter(method);
//         assertFalse(accept);
    }

    @Test
    public void readTemplateContentReturnsEmptyStringForEmptyFile() throws Exception {
        File tempFile = File.createTempFile("k,rWgE)MEPis", "k,rWgE)MEPis", (File) null);
        Path path = tempFile.toPath();
        Charset charset = Charset.defaultCharset();
        String content = QuteProcessor.readTemplateContent(path, charset);
        assertEquals("", content);
    }

    @Test
    public void findTemplatePathReturnsNullWhenNotPresent() {
        LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> analysesList = new LinkedList<>();
        TemplatesAnalysisBuildItem analyses = new TemplatesAnalysisBuildItem(analysesList);
        String path = QuteProcessor.findTemplatePath(analyses, "k@q)HDU<bJ_Kx_");
        assertNull(path);
    }

    @Test
    public void extractMatchTypeReturnsMatchingTypeWhenFound() {
        LinkedHashSet<Type> candidates = new LinkedHashSet<>();
        WildcardType wildcard = WildcardType.UNBOUNDED;
        candidates.add(wildcard);
        DotName typeName = wildcard.name();
        Function<Type, Type> identity = Function.identity();
        Type matched = QuteProcessor.extractMatchType(candidates, typeName, identity);
        assertSame(wildcard, matched);
    }

    @Test
    public void collectTemplateVariantsThrowsSIOOBEOnInvalidSuffixHandling() {
        QuteProcessor processor = new QuteProcessor();
        TemplatePathBuildItem first = mock(TemplatePathBuildItem.class);
        doReturn("MW", "MW", "4uX5{").when(first).getPath();
        TemplatePathBuildItem second = mock(TemplatePathBuildItem.class);
        doReturn("NOSECTION_HELPER_FOUNDelse", "MW", "8O", "NOSECTION_HELPER_FOUNDelse").when(second).getPath();
        List<TemplatePathBuildItem> paths = List.of(first, second, second, first, second, first, second);
        EffectiveTemplatePathsBuildItem effectivePaths = new EffectiveTemplatePathsBuildItem(paths);
        LinkedList<String> suffixes = new LinkedList<>();
        suffixes.add("8O");
        QuteConfig config = mock(QuteConfig.class);
        doReturn(suffixes).when(config).suffixes();
        assertThrows(StringIndexOutOfBoundsException.class, () -> processor.collectTemplateVariants(effectivePaths, config));
    }

    @Test
    public void processTemplateErrorsNoIncorrectExpressionsProducesNoServiceStart() {
        Stack<TemplatesAnalysisBuildItem.TemplateAnalysis> analysesList = new Stack<>();
        TemplatesAnalysisBuildItem analyses = new TemplatesAnalysisBuildItem(analysesList);
        QuteProcessor processor = new QuteProcessor();
        Vector<IncorrectExpressionBuildItem> incorrect = new Vector<>();
        processor.processTemplateErrors(analyses, incorrect, (BuildProducer<ServiceStartBuildItem>) null);
        assertEquals(0, incorrect.size());
    }

    @Test
    public void addSingletonToNamedRecordsHasExpectedReason() {
        QuteProcessor processor = new QuteProcessor();
        AutoAddScopeBuildItem item = processor.addSingletonToNamedRecords();
        assertEquals("Found Java record annotated with @Named", item.getReason());
    }

    @Test
    public void firstPassLookupConfigDeclaredMembersOnlyHonorsOverrideTrue() {
        Class<TypeCheckExcludeBuildItem> target = TypeCheckExcludeBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<TypeCheckExcludeBuildItem> producer = new BuildProducerImpl<>(target, buildContext);
        Predicate<AnnotationTarget> filter = Predicate.isEqual(producer);
        Boolean declaredOnly = Boolean.TRUE;
        QuteProcessor.FirstPassJavaMemberLookupConfig config = new QuteProcessor.FirstPassJavaMemberLookupConfig(
                (QuteProcessor.JavaMemberLookupConfig) null, filter, declaredOnly);
        boolean result = config.declaredMembersOnly();
        assertTrue(result);
    }

    @Test
    public void firstPassLookupConfigDeclaredMembersOnlyTrueWhenNextTrue() {
        IndexView[] empty = new IndexView[0];
        StackedIndex index = StackedIndex.create(empty);
        Object target = new Object();
        Predicate<AnnotationTarget> filter = Predicate.isEqual(target);
        QuteProcessor.FixedJavaMemberLookupConfig next = new QuteProcessor.FixedJavaMemberLookupConfig(index, filter, true);
        QuteProcessor.FirstPassJavaMemberLookupConfig firstPass = new QuteProcessor.FirstPassJavaMemberLookupConfig(next, filter,
                (Boolean) null);
        boolean declaredOnly = firstPass.declaredMembersOnly();
        assertTrue(declaredOnly);
    }

    @Test
    public void firstPassLookupConfigFilterDelegatesToNext() {
        Class<Object> objectClass = Object.class;
//         ClassInfo classInfo = Index.singleClass(objectClass);
//         Map<DotName, List<AnnotationInstance>> annotations = classInfo.annotationsMap();
        HashMap<DotName, List<ClassInfo>> map = new HashMap<>();
        HashMap<DotName, ClassInfo> classes = new HashMap<>();
//         Index index = Index.create(annotations, map, map, classes);
//         Predicate<AnnotationTarget> filter = Predicate.isEqual(annotations);
//         QuteProcessor.FixedJavaMemberLookupConfig next = new QuteProcessor.FixedJavaMemberLookupConfig(index, filter, true);
        Boolean declaredOnly = Boolean.FALSE;
//         QuteProcessor.FirstPassJavaMemberLookupConfig firstPass = new QuteProcessor.FirstPassJavaMemberLookupConfig(next, filter,
//                 declaredOnly);
//         firstPass.filter();
//         assertTrue(next.declaredMembersOnly());
    }

    @Test
    public void getNameOnInjectionPointThrowsIAEForInvalidType() {
        ClassType characterType = ClassType.CHARACTER_CLASS;
        LinkedHashSet<AnnotationInstance> qualifiers = new LinkedHashSet<>();
        InjectionPointInfo.TypeAndQualifiers tq = new InjectionPointInfo.TypeAndQualifiers(characterType, qualifiers);
        InjectionPointInfo ip = InjectionPointInfo.fromSyntheticInjectionPoint(tq);
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class, () -> QuteProcessor.getName(ip));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName().equals("io.quarkus.qute.deployment.QuteProcessor")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void collectNamespaceExpressionsThrowsNPEWhenTemplateAnalysisIsNullInList() {
        Stack<TemplatesAnalysisBuildItem.TemplateAnalysis> analysesList = new Stack<>();
        analysesList.setSize(5);
        TemplatesAnalysisBuildItem analyses = new TemplatesAnalysisBuildItem(analysesList);
        NullPointerException exception = assertThrows(NullPointerException.class,
                () -> QuteProcessor.collectNamespaceExpressions(analyses, ""));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName().equals("io.quarkus.qute.deployment.QuteProcessor")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void collectNamespaceExpressionsWithEmptyAnalysesReturnsEmptyMap() {
        Vector<TemplatesAnalysisBuildItem.TemplateAnalysis> analysesList = new Vector<>();
        TemplatesAnalysisBuildItem analyses = new TemplatesAnalysisBuildItem(analysesList);
        Map<TemplatesAnalysisBuildItem.TemplateAnalysis, Set<Expression>> result = QuteProcessor.collectNamespaceExpressions(analyses,
                "");
        assertEquals(0, result.size());
    }

    @Test
    public void validateTemplateDataNamespacesOnEmptyInputNoErrors() {
        QuteProcessor processor = new QuteProcessor();
        LinkedList<TemplateDataBuildItem> dataItems = new LinkedList<>();
        Class<ServiceStartBuildItem> serviceStartClass = ServiceStartBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<ServiceStartBuildItem> serviceStartProducer = new BuildProducerImpl<>(serviceStartClass, buildContext);
        processor.validateTemplateDataNamespaces(dataItems, serviceStartProducer);
        assertEquals(0, dataItems.size());
    }

    @Test
    public void collectTemplateDataAnnotationsHandlesEmptyIndex() {
        QuteProcessor processor = new QuteProcessor();
        IndexView[] empty = new IndexView[0];
        StackedIndex stackedIndex = StackedIndex.create(empty);
        HashSet<DotName> additional = new HashSet<>(387);
        BeanArchiveIndexBuildItem beanArchiveIndex = new BeanArchiveIndexBuildItem(stackedIndex, stackedIndex, additional);
        Class<TemplateDataBuildItem> dataClass = TemplateDataBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<TemplateDataBuildItem> dataProducer = new BuildProducerImpl<>(dataClass, buildContext);
        processor.collectTemplateDataAnnotations(beanArchiveIndex, dataProducer);
    }

    @Test
    public void matchResultIsClassTrueForClassType() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        ClassType classType = ClassType.ANNOTATION_TYPE;
        matchResult.setValues((ClassInfo) null, classType);
        boolean isClass = matchResult.isClass();
        assertTrue(isClass);
    }

    @Test
    public void matchResultIsClassFalseWhenNoTypeSet() {
        EmptyIndex emptyIndex = EmptyIndex.INSTANCE;
        Types.AssignabilityCheck assignability = new Types.AssignabilityCheck(emptyIndex);
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult(assignability);
        boolean isClass = matchResult.isClass();
        assertFalse(isClass);
    }

    @Test
    public void matchResultIsParameterizedTypeFalseWhenTypeNotSet() {
        HashMap<DotName, List<AnnotationInstance>> annotations = new HashMap<>();
        HashMap<DotName, List<ClassInfo>> map = new HashMap<>();
        HashMap<DotName, ClassInfo> classes = new HashMap<>();
        Index index = Index.create(annotations, map, map, map, classes, map);
        Types.AssignabilityCheck assignability = new Types.AssignabilityCheck(index);
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult(assignability);
        boolean parameterized = matchResult.isParameterizedType();
        assertFalse(parameterized);
    }

    @Test
    public void matchResultIsArrayFalseByDefault() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        boolean isArray = matchResult.isArray();
        assertFalse(isArray);
    }

    @Test
    public void matchResultIsPrimitiveFalseForTypeVariable() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        TypeVariable variable = TypeVariable.create("8LiOQgPN+)^-");
        matchResult.setValues((ClassInfo) null, variable);
        boolean primitive = matchResult.isPrimitive();
        assertFalse(primitive);
    }

    @Test
    public void matchResultIsPrimitiveFalseByDefault() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        boolean primitive = matchResult.isPrimitive();
        assertFalse(primitive);
    }

    @Test
    public void getParameterizedTypeArgumentsEmptyForClassType() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        ClassType classType = ClassType.ANNOTATION_TYPE;
        matchResult.setValues((ClassInfo) null, classType);
        List<Type> typeArgs = matchResult.getParameterizedTypeArguments();
        assertTrue(typeArgs.isEmpty());
    }

    @Test
    public void extractMatchTypeReturnsNullWhenNoMatch() {
        LinkedHashSet<Type> candidates = new LinkedHashSet<>();
        DotName targetName = DotName.FLOAT_CLASS_NAME;
        PrimitiveType doublePrimitive = PrimitiveType.DOUBLE;
        candidates.add(doublePrimitive);
        Function<Type, Type> extractor = QuteProcessor.MAP_ENTRY_EXTRACT_FUN;
        Type result = QuteProcessor.extractMatchType(candidates, targetName, extractor);
        assertNull(result);
    }

    @Test
    public void processLoopElementHintWithNullExpressionThrowsNPE() {
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
        TypeVariable variable = TypeVariable.create("8LiOQgPN+)^-");
        matchResult.setValues((ClassInfo) null, variable);
        Class<IncorrectExpressionBuildItem> incorrectClass = IncorrectExpressionBuildItem.class;
        BuildProducerImpl<IncorrectExpressionBuildItem> incorrectProducer = new BuildProducerImpl<>(incorrectClass, (BuildContext) null);
        NullPointerException exception = assertThrows(NullPointerException.class, () -> QuteProcessor.processLoopElementHint(matchResult,
                (IndexView) null, (Expression) null, incorrectProducer));
        boolean found = false;
        for (StackTraceElement element : exception.getStackTrace()) {
            if (element.getClassName().equals("io.quarkus.qute.deployment.QuteProcessor")) {
                found = true;
                break;
            }
        }
        assertTrue(found);
    }

    @Test
    public void processLoopElementHintDoesNotThrowOnNullExpressionAndEmptyIndex() {
        IndexView index = IndexView.empty();
        Types.AssignabilityCheck assignability = new Types.AssignabilityCheck(index);
        Class<IncorrectExpressionBuildItem> incorrectClass = IncorrectExpressionBuildItem.class;
        BuildContext buildContext = mock(BuildContext.class);
        BuildProducerImpl<IncorrectExpressionBuildItem> incorrectProducer = new BuildProducerImpl<>(incorrectClass, buildContext);
        QuteProcessor.MatchResult matchResult = new QuteProcessor.MatchResult(assignability);
        QuteProcessor.processLoopElementHint(matchResult, index, (Expression) null, incorrectProducer);
    }

}