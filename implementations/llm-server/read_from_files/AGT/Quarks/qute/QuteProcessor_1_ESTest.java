package io.quarkus.qute.deployment;

import org.junit.Test;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;
import static org.evosuite.runtime.EvoAssertions.*;
import io.quarkus.arc.deployment.AdditionalBeanBuildItem;
import io.quarkus.arc.deployment.AutoAddScopeBuildItem;
import io.quarkus.arc.deployment.BeanArchiveIndexBuildItem;
import io.quarkus.arc.deployment.BeanContainerBuildItem;
import io.quarkus.arc.deployment.BeanDefiningAnnotationBuildItem;
import io.quarkus.arc.deployment.CompletedApplicationClassPredicateBuildItem;
import io.quarkus.arc.deployment.QualifierRegistrarBuildItem;
import io.quarkus.arc.deployment.SynthesisFinishedBuildItem;
import io.quarkus.arc.deployment.SyntheticBeanBuildItem;
import io.quarkus.arc.deployment.ValidationPhaseBuildItem;
import io.quarkus.arc.processor.BeanDeployment;
import io.quarkus.arc.processor.BeanDeploymentValidator;
import io.quarkus.arc.processor.BeanInfo;
import io.quarkus.arc.processor.BeanProcessor;
import io.quarkus.arc.processor.BeanResolver;
import io.quarkus.arc.processor.InjectionPointInfo;
import io.quarkus.arc.processor.ObserverInfo;
import io.quarkus.arc.runtime.BeanContainer;
import io.quarkus.bootstrap.model.ApplicationModelBuilder;
import io.quarkus.bootstrap.model.DefaultApplicationModel;
import io.quarkus.builder.BuildContext;
import io.quarkus.deployment.ApplicationArchive;
import io.quarkus.deployment.ApplicationArchiveImpl;
import io.quarkus.deployment.BuildProducerImpl;
import io.quarkus.deployment.annotations.BuildProducer;
import io.quarkus.deployment.builditem.ApplicationArchivesBuildItem;
import io.quarkus.deployment.builditem.BytecodeTransformerBuildItem;
import io.quarkus.deployment.builditem.FeatureBuildItem;
import io.quarkus.deployment.builditem.GeneratedClassBuildItem;
import io.quarkus.deployment.builditem.GeneratedResourceBuildItem;
import io.quarkus.deployment.builditem.HotDeploymentWatchedFileBuildItem;
import io.quarkus.deployment.builditem.LaunchModeBuildItem;
import io.quarkus.deployment.builditem.LiveReloadBuildItem;
import io.quarkus.deployment.builditem.ServiceStartBuildItem;
import io.quarkus.deployment.builditem.nativeimage.NativeImageResourceBuildItem;
import io.quarkus.deployment.builditem.nativeimage.ReflectiveClassBuildItem;
import io.quarkus.deployment.dev.AlwaysFalsePredicate;
import io.quarkus.deployment.pkg.NativeConfig;
import io.quarkus.deployment.pkg.builditem.CurateOutcomeBuildItem;
import io.quarkus.dev.spi.DevModeType;
import io.quarkus.maven.dependency.ResolvedArtifactDependency;
import io.quarkus.maven.dependency.ResolvedDependency;
import io.quarkus.maven.dependency.ResolvedDependencyBuilder;
import io.quarkus.panache.common.deployment.PanacheEntityClassesBuildItem;
import io.quarkus.paths.DirectoryPathTree;
import io.quarkus.paths.EmptyPathTree;
import io.quarkus.qute.Expression;
import io.quarkus.qute.LoopSectionHelper;
import io.quarkus.qute.SetSectionHelper;
import io.quarkus.qute.TemplateNode;
import io.quarkus.qute.deployment.CheckedFragmentValidationBuildItem;
import io.quarkus.qute.deployment.CheckedTemplateAdapter;
import io.quarkus.qute.deployment.CheckedTemplateAdapterBuildItem;
import io.quarkus.qute.deployment.CheckedTemplateBuildItem;
import io.quarkus.qute.deployment.CustomTemplateLocatorPatternsBuildItem;
import io.quarkus.qute.deployment.EffectiveTemplatePathsBuildItem;
import io.quarkus.qute.deployment.EngineConfigurationsBuildItem;
import io.quarkus.qute.deployment.GeneratedValueResolverBuildItem;
import io.quarkus.qute.deployment.ImplicitValueResolverBuildItem;
import io.quarkus.qute.deployment.IncorrectExpressionBuildItem;
import io.quarkus.qute.deployment.MessageBundleMethodBuildItem;
import io.quarkus.qute.deployment.QuteProcessor;
import io.quarkus.qute.deployment.TemplateDataBuildItem;
import io.quarkus.qute.deployment.TemplateExpressionMatchesBuildItem;
import io.quarkus.qute.deployment.TemplateExtensionMethodBuildItem;
import io.quarkus.qute.deployment.TemplateFilePathsBuildItem;
import io.quarkus.qute.deployment.TemplateGlobalBuildItem;
import io.quarkus.qute.deployment.TemplateGlobalProviderBuildItem;
import io.quarkus.qute.deployment.TemplatePathBuildItem;
import io.quarkus.qute.deployment.TemplatePathExcludeBuildItem;
import io.quarkus.qute.deployment.TemplateRootBuildItem;
import io.quarkus.qute.deployment.TemplateRootsBuildItem;
import io.quarkus.qute.deployment.TemplateVariantsBuildItem;
import io.quarkus.qute.deployment.TemplatesAnalysisBuildItem;
import io.quarkus.qute.deployment.TypeCheckExcludeBuildItem;
import io.quarkus.qute.deployment.Types;
import io.quarkus.qute.deployment.ValidationParserHookBuildItem;
import io.quarkus.qute.runtime.QuteConfig;
import io.quarkus.qute.runtime.QuteRecorder;
import io.quarkus.qute.runtime.QuteTestModeConfig;
import io.quarkus.runtime.LaunchMode;
import java.io.File;
import java.io.UncheckedIOException;
import java.lang.reflect.Array;
import java.net.URI;
import java.nio.charset.Charset;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.PriorityQueue;
import java.util.Set;
import java.util.Stack;
import java.util.TreeSet;
import java.util.Vector;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.LinkedTransferQueue;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.regex.Pattern;
import org.evosuite.runtime.EvoRunnerParameters;
import org.evosuite.runtime.ViolatedAssumptionAnswer;
import org.evosuite.runtime.mock.java.lang.MockThrowable;
import org.jboss.jandex.AnnotationInstance;
import org.jboss.jandex.AnnotationTarget;
import org.jboss.jandex.AnnotationValue;
import org.jboss.jandex.ArrayType;
import org.jboss.jandex.ClassInfo;
import org.jboss.jandex.ClassType;
import org.jboss.jandex.CompositeIndex;
import org.jboss.jandex.DotName;
import org.jboss.jandex.EmptyIndex;
import org.jboss.jandex.FieldInfo;
import org.jboss.jandex.Index;
import org.jboss.jandex.IndexView;
import org.jboss.jandex.MethodInfo;
import org.jboss.jandex.ParameterizedType;
import org.jboss.jandex.PrimitiveType;
import org.jboss.jandex.StackedIndex;
import org.jboss.jandex.Type;
import org.jboss.jandex.TypeVariable;
import org.jboss.jandex.WildcardType;
import org.junit.runner.RunWith;

public class QuteProcessor_1_ESTest extends QuteProcessor_1_ESTest_scaffolding {

  @Test(timeout = 4000)
  public void testIsEmpty()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      boolean boolean0 = quteProcessor_MatchResult0.isEmpty();
      assertTrue(boolean0);
  }

  @Test(timeout = 4000)
  public void testGetParameterizedTypeArguments()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      // Undeclared exception!
      try { 
        quteProcessor_MatchResult0.getParameterizedTypeArguments();
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"org.jboss.jandex.Type.kind()\" because \"this.type\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor$MatchResult", e);
      }
  }

  @Test(timeout = 4000)
  public void testAutoExtractType()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      quteProcessor_MatchResult0.autoExtractType();
  }

  @Test(timeout = 4000)
  public void testFilterAndFilter()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(emptyIndex0, (Predicate<AnnotationTarget>) null, false);
      quteProcessor_FixedJavaMemberLookupConfig0.filter();
      assertFalse(quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly());
  }

  @Test(timeout = 4000)
  public void testCollectNamespaceExpressionsTaking1And1ThrowsNullPointerExceptionAndCollectNamespaceExpressionsTaking1And10()  throws Throwable  {
      // Undeclared exception!
      try { 
        QuteProcessor.collectNamespaceExpressions((TemplatesAnalysisBuildItem.TemplateAnalysis) null, "");
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot read field \"expressions\" because \"analysis\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testExtractMatchTypeReturningNonNull()  throws Throwable  {
      HashSet<Type> hashSet0 = new HashSet<Type>();
      DotName dotName0 = DotName.BYTE_CLASS_NAME;
      Type[] typeArray0 = new Type[2];
      ParameterizedType parameterizedType0 = ParameterizedType.create(dotName0, typeArray0);
      hashSet0.add(parameterizedType0);
      Function<Type, Type> function0 = QuteProcessor.MAP_ENTRY_EXTRACT_FUN;
      Type type0 = QuteProcessor.extractMatchType(hashSet0, dotName0, function0);
      assertNotSame(type0, parameterizedType0);
  }

  @Test(timeout = 4000)
  public void testValidateExpressionsThrowsNullPointerException()  throws Throwable  {
      LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedList0 = new LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(linkedList0);
      Class<IncorrectExpressionBuildItem> class0 = IncorrectExpressionBuildItem.class;
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class0, (BuildContext) null);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<Object>[] classArray0 = (Class<Object>[]) Array.newInstance(Class.class, 2);
      Class<Object> class1 = Object.class;
      classArray0[0] = class1;
      classArray0[1] = classArray0[0];
      Index index0 = Index.of(classArray0);
      DotName dotName0 = DotName.CLASS_NAME;
      Set<DotName> set0 = index0.getSubpackages(dotName0);
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(index0, index0, set0);
      Vector<TemplateExtensionMethodBuildItem> vector0 = new Vector<TemplateExtensionMethodBuildItem>();
      LinkedList<TypeCheckExcludeBuildItem> linkedList1 = new LinkedList<TypeCheckExcludeBuildItem>();
      Class<ImplicitValueResolverBuildItem> class2 = ImplicitValueResolverBuildItem.class;
      BuildProducerImpl<ImplicitValueResolverBuildItem> buildProducerImpl1 = new BuildProducerImpl<ImplicitValueResolverBuildItem>(class2, (BuildContext) null);
      BuildProducer<TemplateExpressionMatchesBuildItem> buildProducer0 = (BuildProducer<TemplateExpressionMatchesBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BeanDeployment beanDeployment0 = mock(BeanDeployment.class, new ViolatedAssumptionAnswer());
      doReturn((BeanResolver) null).when(beanDeployment0).getBeanResolver();
      doReturn((Collection) null).when(beanDeployment0).getBeans();
      doReturn((Collection) null).when(beanDeployment0).getInjectionPoints();
      doReturn((Collection) null).when(beanDeployment0).getObservers();
      SynthesisFinishedBuildItem synthesisFinishedBuildItem0 = new SynthesisFinishedBuildItem(beanDeployment0);
      LinkedList<CheckedTemplateBuildItem> linkedList2 = new LinkedList<CheckedTemplateBuildItem>();
      SynchronousQueue<TemplateDataBuildItem> synchronousQueue0 = new SynchronousQueue<TemplateDataBuildItem>();
      List<TemplateDataBuildItem> list0 = List.copyOf(synchronousQueue0);
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      NativeConfig nativeConfig0 = mock(NativeConfig.class, new ViolatedAssumptionAnswer());
      ArrayList<TemplateGlobalBuildItem> arrayList0 = new ArrayList<TemplateGlobalBuildItem>();
      // Undeclared exception!
      try { 
        quteProcessor0.validateExpressions(templatesAnalysisBuildItem0, beanArchiveIndexBuildItem0, vector0, linkedList1, buildProducerImpl0, buildProducerImpl1, buildProducer0, synthesisFinishedBuildItem0, linkedList2, list0, quteConfig0, nativeConfig0, arrayList0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Beans collection is null
         //
         verifyException("java.util.Objects", e);
      }
  }

  @Test(timeout = 4000)
  public void testReadTemplateContentThrowsUncheckedIOException()  throws Throwable  {
      File file0 = File.createTempFile(">CB2B?chR;(`fsL7", ">CB2B?chR;(`fsL7");
      File file1 = new File(file0, ">CB2B?chR;(`fsL7");
      Path path0 = file1.toPath();
      Charset charset0 = Charset.defaultCharset();
      // Undeclared exception!
      try { 
        QuteProcessor.readTemplateContent(path0, charset0);
        fail("Expecting exception: UncheckedIOException");
      
      } catch(UncheckedIOException e) {
         //
         // Unable to read the template content from path: /var/folders/vs/gv1n7dk51wb23kfs_2s1cdvc0000gn/T/>CB2B?chR;(`fsL72209428880324989404>CB2B?chR;(`fsL7/>CB2B?chR;(`fsL7
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessTemplateErrorsThrowsRuntimeException0()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis> arrayList0 = new ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(arrayList0);
      LinkedList<IncorrectExpressionBuildItem> linkedList0 = new LinkedList<IncorrectExpressionBuildItem>();
      TemplateNode.Origin templateNode_Origin0 = mock(TemplateNode.Origin.class, new ViolatedAssumptionAnswer());
      doReturn(0).when(templateNode_Origin0).getLine();
      doReturn(0).when(templateNode_Origin0).getLineCharacterStart();
      doReturn((String) null).when(templateNode_Origin0).getTemplateGeneratedId();
      doReturn((String) null).when(templateNode_Origin0).getTemplateId();
      IncorrectExpressionBuildItem incorrectExpressionBuildItem0 = new IncorrectExpressionBuildItem((String) null, (String) null, (String) null, templateNode_Origin0, (String) null);
      linkedList0.add(incorrectExpressionBuildItem0);
      // Undeclared exception!
      try { 
        quteProcessor0.processTemplateErrors(templatesAnalysisBuildItem0, linkedList0, (BuildProducer<ServiceStartBuildItem>) null);
        fail("Expecting exception: RuntimeException");
      
      } catch(RuntimeException e) {
         //
         // Found incorrect expressions (1):
         // \t[1] :java.lang.Integer@0000000120:java.lang.Integer@0000000120 - {}: @Named bean not found for []
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollectTemplateGlobals()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      IndexView indexView0 = IndexView.empty();
      SynchronousQueue<IndexView> synchronousQueue0 = new SynchronousQueue<IndexView>();
      CompositeIndex compositeIndex0 = CompositeIndex.create((Collection<IndexView>) synchronousQueue0);
      DotName dotName0 = QuteProcessor.LOCATION;
      Set<DotName> set0 = compositeIndex0.getSubpackages(dotName0);
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(indexView0, indexView0, set0);
      Class<TemplateGlobalBuildItem> class0 = TemplateGlobalBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TemplateGlobalBuildItem> buildProducerImpl0 = new BuildProducerImpl<TemplateGlobalBuildItem>(class0, buildContext0);
      quteProcessor0.collectTemplateGlobals(beanArchiveIndexBuildItem0, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testCollectNamespaceExpressionsTaking3ArgumentsThrowsNullPointerException()  throws Throwable  {
      LinkedHashSet<Expression> linkedHashSet0 = new LinkedHashSet<Expression>();
      // Undeclared exception!
      try { 
        QuteProcessor.collectNamespaceExpressions((Expression) null, linkedHashSet0, "zC7wEVFxeubcelse");
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.qute.Expression.isLiteral()\" because \"expression\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testFindTemplatePath()  throws Throwable  {
      LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedList0 = new LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(linkedList0);
      String string0 = QuteProcessor.findTemplatePath(templatesAnalysisBuildItem0, "NARAYNA_STM");
      assertNull(string0);
  }

  @Test(timeout = 4000)
  public void testExtractMatchTypeReturningNull()  throws Throwable  {
      TreeSet<Type> treeSet0 = new TreeSet<Type>();
      DotName dotName0 = DotName.ENUM_NAME;
      Function<Type, Type> function0 = Function.identity();
      Type type0 = QuteProcessor.extractMatchType(treeSet0, dotName0, function0);
      assertNull(type0);
  }

  @Test(timeout = 4000)
  public void testDeclaredMembersOnly0()  throws Throwable  {
      AlwaysFalsePredicate<Object> alwaysFalsePredicate0 = new AlwaysFalsePredicate<Object>();
      Predicate<AnnotationTarget> predicate0 = Predicate.not(alwaysFalsePredicate0);
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig((IndexView) null, predicate0, false);
      Boolean boolean0 = Boolean.valueOf(false);
      QuteProcessor.FirstPassJavaMemberLookupConfig quteProcessor_FirstPassJavaMemberLookupConfig0 = new QuteProcessor.FirstPassJavaMemberLookupConfig(quteProcessor_FixedJavaMemberLookupConfig0, predicate0, boolean0);
      quteProcessor_FirstPassJavaMemberLookupConfig0.declaredMembersOnly();
      assertFalse(quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly());
  }

  @Test(timeout = 4000)
  public void testDeclaredMembersOnly1()  throws Throwable  {
      HashMap<DotName, List<AnnotationInstance>> hashMap0 = new HashMap<DotName, List<AnnotationInstance>>();
      HashMap<DotName, List<ClassInfo>> hashMap1 = new HashMap<DotName, List<ClassInfo>>();
      HashMap<DotName, ClassInfo> hashMap2 = new HashMap<DotName, ClassInfo>();
      Index index0 = Index.create((Map<DotName, List<AnnotationInstance>>) hashMap0, (Map<DotName, List<ClassInfo>>) hashMap1, (Map<DotName, List<ClassInfo>>) hashMap1, (Map<DotName, List<ClassInfo>>) hashMap1, (Map<DotName, ClassInfo>) hashMap2, (Map<DotName, List<ClassInfo>>) hashMap1);
      AlwaysFalsePredicate<AnnotationTarget> alwaysFalsePredicate0 = new AlwaysFalsePredicate<AnnotationTarget>();
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(index0, alwaysFalsePredicate0, false);
      QuteProcessor.FirstPassJavaMemberLookupConfig quteProcessor_FirstPassJavaMemberLookupConfig0 = new QuteProcessor.FirstPassJavaMemberLookupConfig(quteProcessor_FixedJavaMemberLookupConfig0, alwaysFalsePredicate0, (Boolean) null);
      boolean boolean0 = quteProcessor_FirstPassJavaMemberLookupConfig0.declaredMembersOnly();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testInitDefaultMembersFilter()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Predicate<AnnotationTarget> predicate0 = QuteProcessor.initDefaultMembersFilter();
      Boolean boolean0 = Boolean.TRUE;
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(emptyIndex0, predicate0, false);
      QuteProcessor.FirstPassJavaMemberLookupConfig quteProcessor_FirstPassJavaMemberLookupConfig0 = new QuteProcessor.FirstPassJavaMemberLookupConfig(quteProcessor_FixedJavaMemberLookupConfig0, predicate0, boolean0);
      quteProcessor_FirstPassJavaMemberLookupConfig0.filter();
      assertFalse(quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly());
  }

  @Test(timeout = 4000)
  public void testGetNameTakingInjectionPointInfoThrowsIllegalArgumentException()  throws Throwable  {
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      TreeSet<AnnotationInstance> treeSet0 = new TreeSet<AnnotationInstance>();
      InjectionPointInfo.TypeAndQualifiers injectionPointInfo_TypeAndQualifiers0 = new InjectionPointInfo.TypeAndQualifiers(primitiveType0, treeSet0);
      InjectionPointInfo injectionPointInfo0 = InjectionPointInfo.fromSyntheticInjectionPoint(injectionPointInfo_TypeAndQualifiers0);
      // Undeclared exception!
      try { 
        QuteProcessor.getName(injectionPointInfo0);
        fail("Expecting exception: IllegalArgumentException");
      
      } catch(IllegalArgumentException e) {
         //
         // no message in exception (getMessage() returned null)
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollectNamespaceExpressionsTaking1And1ThrowsNullPointerExceptionAndCollectNamespaceExpressionsTaking1And11()  throws Throwable  {
      LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedList0 = new LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      linkedList0.add((TemplatesAnalysisBuildItem.TemplateAnalysis) null);
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(linkedList0);
      // Undeclared exception!
      try { 
        QuteProcessor.collectNamespaceExpressions(templatesAnalysisBuildItem0, "cursor_add(ess");
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot read field \"expressions\" because \"analysis\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollectNamespaceExpressionsTaking1And1()  throws Throwable  {
      LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedList0 = new LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(linkedList0);
      Map<TemplatesAnalysisBuildItem.TemplateAnalysis, Set<Expression>> map0 = QuteProcessor.collectNamespaceExpressions(templatesAnalysisBuildItem0, (String) null);
      assertTrue(map0.isEmpty());
  }

  @Test(timeout = 4000)
  public void testValidateTemplateDataNamespacesThrowsRuntimeException()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      DotName dotName0 = DotName.ENUM_NAME;
      Stack<AnnotationValue> stack0 = new Stack<AnnotationValue>();
      AnnotationInstance annotationInstance0 = AnnotationInstance.create(dotName0, (AnnotationTarget) classInfo0, (List<AnnotationValue>) stack0);
      TemplateDataBuildItem templateDataBuildItem0 = new TemplateDataBuildItem(annotationInstance0, classInfo0);
      List<TemplateDataBuildItem> list0 = List.of(templateDataBuildItem0, templateDataBuildItem0, templateDataBuildItem0, templateDataBuildItem0);
      // Undeclared exception!
      try { 
        quteProcessor0.validateTemplateDataNamespaces(list0, (BuildProducer<ServiceStartBuildItem>) null);
        fail("Expecting exception: RuntimeException");
      
      } catch(RuntimeException e) {
         //
         // The namespace [java_lang_Object] is defined by multiple @TemplateData and/or @TemplateEnum annotations; make sure the annotation declared on the following classes do not collide:
         // \t- java.lang.Object
         // \t- java.lang.Object
         // \t- java.lang.Object
         // \t- java.lang.Object
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testValidateTemplateDataNamespaces()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      DotName dotName0 = DotName.ENUM_NAME;
      AnnotationValue annotationValue0 = AnnotationValue.createEnumValue("CkY$thHhSGV[", dotName0, " Mr");
      List<AnnotationValue> list0 = List.of(annotationValue0, annotationValue0, annotationValue0, annotationValue0, annotationValue0, annotationValue0, annotationValue0, annotationValue0, annotationValue0, annotationValue0);
      AnnotationInstance annotationInstance0 = AnnotationInstance.create(dotName0, (AnnotationTarget) classInfo0, list0);
      TemplateDataBuildItem templateDataBuildItem0 = new TemplateDataBuildItem(annotationInstance0, classInfo0);
      List<TemplateDataBuildItem> list1 = List.of(templateDataBuildItem0);
      Class<ServiceStartBuildItem> class1 = ServiceStartBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<ServiceStartBuildItem> buildProducerImpl0 = new BuildProducerImpl<ServiceStartBuildItem>(class1, buildContext0);
      quteProcessor0.validateTemplateDataNamespaces(list1, buildProducerImpl0);
      assertEquals(1, list1.size());
  }

  @Test(timeout = 4000)
  public void testCollectTemplateDataAnnotations()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      LinkedHashSet<DotName> linkedHashSet0 = new LinkedHashSet<DotName>();
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, linkedHashSet0);
      Class<TemplateDataBuildItem> class0 = TemplateDataBuildItem.class;
      BuildProducerImpl<TemplateDataBuildItem> buildProducerImpl0 = new BuildProducerImpl<TemplateDataBuildItem>(class0, (BuildContext) null);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      quteProcessor0.collectTemplateDataAnnotations(beanArchiveIndexBuildItem0, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testIsClass()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      boolean boolean0 = quteProcessor_MatchResult0.isClass();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testIsParameterizedType()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      boolean boolean0 = quteProcessor_MatchResult0.isParameterizedType();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testIsArray()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      boolean boolean0 = quteProcessor_MatchResult0.isArray();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testIsPrimitive0()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      WildcardType wildcardType0 = WildcardType.UNBOUNDED;
      quteProcessor_MatchResult0.setValues((ClassInfo) null, wildcardType0);
      boolean boolean0 = quteProcessor_MatchResult0.isPrimitive();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testIsPrimitive1()  throws Throwable  {
      CompositeIndex[] compositeIndexArray0 = new CompositeIndex[0];
      CompositeIndex compositeIndex0 = CompositeIndex.createMerged(compositeIndexArray0);
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(compositeIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      PrimitiveType primitiveType0 = PrimitiveType.CHAR;
      quteProcessor_MatchResult0.setValues((ClassInfo) null, primitiveType0);
      boolean boolean0 = quteProcessor_MatchResult0.isPrimitive();
      assertTrue(boolean0);
  }

  @Test(timeout = 4000)
  public void testCreatesMatchResultTakingTypes$AssignabilityCheckAndCallsIsPrimitive()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      boolean boolean0 = quteProcessor_MatchResult0.isPrimitive();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHintThrowsNullPointerException0()  throws Throwable  {
      Class<String> class0 = String.class;
      WildcardType wildcardType0 = WildcardType.createUpperBound(class0);
      Class<Object> class1 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class1);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      quteProcessor_MatchResult0.setValues(classInfo0, wildcardType0);
      Class<IncorrectExpressionBuildItem> class2 = IncorrectExpressionBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class2, buildContext0);
      // Undeclared exception!
      try { 
        QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, emptyIndex0, (Expression) null, buildProducerImpl0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.qute.Expression.toOriginalString()\" because \"expression\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHintThrowsNullPointerException1()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      ClassType classType0 = ClassType.FLOAT_CLASS;
      quteProcessor_MatchResult0.setValues(classInfo0, classType0);
      Class<IncorrectExpressionBuildItem> class1 = IncorrectExpressionBuildItem.class;
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class1, (BuildContext) null);
      // Undeclared exception!
      try { 
        QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, emptyIndex0, (Expression) null, buildProducerImpl0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.qute.Expression.toOriginalString()\" because \"expression\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHint0()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      Class<ResolvedDependencyBuilder> class1 = ResolvedDependencyBuilder.class;
      Type type0 = Type.create(class1);
      ArrayType arrayType0 = ArrayType.create(type0, 62);
      quteProcessor_MatchResult0.setValues(classInfo0, arrayType0);
      Class<IncorrectExpressionBuildItem> class2 = IncorrectExpressionBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class2, buildContext0);
      QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, emptyIndex0, (Expression) null, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHintThrowsIndexOutOfBoundsException()  throws Throwable  {
      Class<TemplateExtensionMethodBuildItem> class0 = TemplateExtensionMethodBuildItem.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      Class<Object> class1 = Object.class;
      Type[] typeArray0 = new Type[6];
      ParameterizedType parameterizedType0 = ParameterizedType.create(class1, typeArray0);
      quteProcessor_MatchResult0.setValues(classInfo0, parameterizedType0);
      Class<IncorrectExpressionBuildItem> class2 = IncorrectExpressionBuildItem.class;
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class2, (BuildContext) null);
      // Undeclared exception!
      try { 
        QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, emptyIndex0, (Expression) null, buildProducerImpl0);
        fail("Expecting exception: IndexOutOfBoundsException");
      
      } catch(IndexOutOfBoundsException e) {
         //
         // Index: 0
         //
         verifyException("java.util.Collections$EmptyList", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHint1()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      ClassType classType0 = ClassType.INTEGER_CLASS;
      quteProcessor_MatchResult0.setValues(classInfo0, classType0);
      Class<IncorrectExpressionBuildItem> class1 = IncorrectExpressionBuildItem.class;
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class1, (BuildContext) null);
      QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, emptyIndex0, (Expression) null, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHint2()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      PrimitiveType primitiveType0 = PrimitiveType.INT;
      quteProcessor_MatchResult0.setValues((ClassInfo) null, primitiveType0);
      Class<IncorrectExpressionBuildItem> class0 = IncorrectExpressionBuildItem.class;
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class0, (BuildContext) null);
      QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, emptyIndex0, (Expression) null, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testProcessLoopElementHintWithNull()  throws Throwable  {
      Class<IncorrectExpressionBuildItem> class0 = IncorrectExpressionBuildItem.class;
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class0, (BuildContext) null);
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck((IndexView) null);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      QuteProcessor.processLoopElementHint(quteProcessor_MatchResult0, (IndexView) null, (Expression) null, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testInitializeThrowsIllegalArgumentException()  throws Throwable  {
      QuteRecorder quteRecorder0 = new QuteRecorder();
      TreeSet<String> treeSet0 = new TreeSet<String>();
      TemplateRootsBuildItem templateRootsBuildItem0 = new TemplateRootsBuildItem(treeSet0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      LinkedList<TemplatePathBuildItem> linkedList0 = new LinkedList<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(linkedList0);
      HashMap<String, List<String>> hashMap0 = new HashMap<String, List<String>>();
      TemplateVariantsBuildItem templateVariantsBuildItem0 = new TemplateVariantsBuildItem(hashMap0);
      Optional<TemplateVariantsBuildItem> optional0 = Optional.ofNullable(templateVariantsBuildItem0);
      Vector<TemplatePathExcludeBuildItem> vector0 = new Vector<TemplatePathExcludeBuildItem>();
      // Undeclared exception!
      try { 
        quteProcessor0.initialize((BuildProducer<SyntheticBeanBuildItem>) null, quteRecorder0, effectiveTemplatePathsBuildItem0, optional0, templateRootsBuildItem0, vector0);
        fail("Expecting exception: IllegalArgumentException");
      
      } catch(IllegalArgumentException e) {
         //
         // The object is not a proxy returned from a recorder method: io.quarkus.qute.runtime.QuteRecorder$1@0000000009
         //
         verifyException("io.quarkus.arc.deployment.SyntheticBeanBuildItem$ExtendedBeanConfigurator", e);
      }
  }

  @Test(timeout = 4000)
  public void testInitializeWithNullAndNonEmptyList()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<SyntheticBeanBuildItem> class0 = SyntheticBeanBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<SyntheticBeanBuildItem> buildProducerImpl0 = new BuildProducerImpl<SyntheticBeanBuildItem>(class0, buildContext0);
      QuteRecorder quteRecorder0 = new QuteRecorder();
      Vector<TemplatePathBuildItem> vector0 = new Vector<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(vector0);
      Optional<TemplateVariantsBuildItem> optional0 = Optional.empty();
      TemplatePathExcludeBuildItem templatePathExcludeBuildItem0 = new TemplatePathExcludeBuildItem("global");
      List<TemplatePathExcludeBuildItem> list0 = List.of(templatePathExcludeBuildItem0, templatePathExcludeBuildItem0);
      // Undeclared exception!
      try { 
        quteProcessor0.initialize(buildProducerImpl0, quteRecorder0, effectiveTemplatePathsBuildItem0, optional0, (TemplateRootsBuildItem) null, list0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.qute.deployment.TemplateRootsBuildItem.getPaths()\" because \"templateRoots\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testInitializeWithNull()  throws Throwable  {
      QuteRecorder quteRecorder0 = new QuteRecorder();
      TreeSet<String> treeSet0 = new TreeSet<String>();
      TemplateRootsBuildItem templateRootsBuildItem0 = new TemplateRootsBuildItem(treeSet0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<SyntheticBeanBuildItem> class0 = SyntheticBeanBuildItem.class;
      BuildProducerImpl<SyntheticBeanBuildItem> buildProducerImpl0 = new BuildProducerImpl<SyntheticBeanBuildItem>(class0, (BuildContext) null);
      ArrayList<TemplatePathExcludeBuildItem> arrayList0 = new ArrayList<TemplatePathExcludeBuildItem>();
      URI uRI0 = URI.create("xk");
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn("xk", "xk").when(templatePathBuildItem0).getContent();
      doReturn("xk", "xk", (String) null, "-SNAPSHOT", (String) null).when(templatePathBuildItem0).getPath();
      doReturn(uRI0, uRI0, uRI0, uRI0, (URI) null).when(templatePathBuildItem0).getSource();
      doReturn(false, true, false, true).when(templatePathBuildItem0).isFileBased();
      List<TemplatePathBuildItem> list0 = List.of(templatePathBuildItem0, templatePathBuildItem0, templatePathBuildItem0, templatePathBuildItem0);
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(list0);
      // Undeclared exception!
      try { 
        quteProcessor0.initialize(buildProducerImpl0, quteRecorder0, effectiveTemplatePathsBuildItem0, (Optional<TemplateVariantsBuildItem>) null, templateRootsBuildItem0, arrayList0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"java.util.Optional.isPresent()\" because \"templateVariants\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollecTemplateContents()  throws Throwable  {
      IndexView indexView0 = IndexView.empty();
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(indexView0, indexView0, (Set<DotName>) null);
      ArrayList<CheckedTemplateAdapterBuildItem> arrayList0 = new ArrayList<CheckedTemplateAdapterBuildItem>();
      QuteProcessor quteProcessor0 = new QuteProcessor();
      quteProcessor0.collecTemplateContents(beanArchiveIndexBuildItem0, arrayList0, (BuildProducer<TemplatePathBuildItem>) null);
      assertEquals(0, arrayList0.size());
  }

  @Test(timeout = 4000)
  public void testExcludeTypeChecksThrowsNullPointerException()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Stack<String> stack0 = new Stack<String>();
      Optional.of(stack0);
      stack0.add(";\"!");
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn((Optional) null).when(quteConfig0).typeCheckExcludes();
      Class<TypeCheckExcludeBuildItem> class0 = TypeCheckExcludeBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TypeCheckExcludeBuildItem> buildProducerImpl0 = new BuildProducerImpl<TypeCheckExcludeBuildItem>(class0, buildContext0);
      // Undeclared exception!
      try { 
        quteProcessor0.excludeTypeChecks(quteConfig0, buildProducerImpl0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"java.util.Optional.isPresent()\" because the return value of \"io.quarkus.qute.runtime.QuteConfig.typeCheckExcludes()\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollectTemplateVariants()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Vector<TemplatePathBuildItem> vector0 = new Vector<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(vector0);
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn("@[M", "@egMelse").when(templatePathBuildItem0).getPath();
      vector0.add(templatePathBuildItem0);
      LoopSectionHelper.Factory loopSectionHelper_Factory0 = new LoopSectionHelper.Factory();
      List<String> list0 = loopSectionHelper_Factory0.getBlockLabels();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(list0).when(quteConfig0).suffixes();
      quteProcessor0.collectTemplateFilePaths(quteConfig0, effectiveTemplatePathsBuildItem0);
      QuteConfig quteConfig1 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(list0).when(quteConfig1).suffixes();
      TemplateVariantsBuildItem templateVariantsBuildItem0 = quteProcessor0.collectTemplateVariants(effectiveTemplatePathsBuildItem0, quteConfig1);
      assertNotNull(templateVariantsBuildItem0);
  }

  @Test(timeout = 4000)
  public void testCollectEngineConfigurations()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      TreeSet<DotName> treeSet0 = new TreeSet<DotName>();
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, treeSet0);
      quteProcessor0.collectEngineConfigurations(beanArchiveIndexBuildItem0, (BuildProducer<EngineConfigurationsBuildItem>) null, (BuildProducer<ValidationPhaseBuildItem.ValidationErrorBuildItem>) null);
  }

  @Test(timeout = 4000)
  public void testValidateTemplateInjectionPoints()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      LinkedBlockingQueue<InjectionPointInfo> linkedBlockingQueue0 = new LinkedBlockingQueue<InjectionPointInfo>();
      BeanDeploymentValidator.ValidationContext beanDeploymentValidator_ValidationContext0 = mock(BeanDeploymentValidator.ValidationContext.class, new ViolatedAssumptionAnswer());
      doReturn(linkedBlockingQueue0).when(beanDeploymentValidator_ValidationContext0).getInjectionPoints();
      BeanProcessor beanProcessor0 = mock(BeanProcessor.class, new ViolatedAssumptionAnswer());
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      TreeSet<AnnotationInstance> treeSet0 = new TreeSet<AnnotationInstance>();
      InjectionPointInfo.TypeAndQualifiers injectionPointInfo_TypeAndQualifiers0 = new InjectionPointInfo.TypeAndQualifiers(primitiveType0, treeSet0);
      InjectionPointInfo injectionPointInfo0 = InjectionPointInfo.fromSyntheticInjectionPoint(injectionPointInfo_TypeAndQualifiers0);
      TimeUnit timeUnit0 = TimeUnit.HOURS;
      linkedBlockingQueue0.offer(injectionPointInfo0, 0L, timeUnit0);
      ValidationPhaseBuildItem validationPhaseBuildItem0 = new ValidationPhaseBuildItem(beanDeploymentValidator_ValidationContext0, beanProcessor0);
      Class<ValidationPhaseBuildItem.ValidationErrorBuildItem> class0 = ValidationPhaseBuildItem.ValidationErrorBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem> buildProducerImpl0 = new BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem>(class0, buildContext0);
      Stack<Pattern> stack0 = new Stack<Pattern>();
      CustomTemplateLocatorPatternsBuildItem customTemplateLocatorPatternsBuildItem0 = new CustomTemplateLocatorPatternsBuildItem(stack0);
      quteProcessor0.validateTemplateInjectionPoints((TemplateFilePathsBuildItem) null, (EffectiveTemplatePathsBuildItem) null, validationPhaseBuildItem0, buildProducerImpl0, customTemplateLocatorPatternsBuildItem0);
  }

  @Test(timeout = 4000)
  public void testCollectTemplateFilePaths()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Stack<TemplatePathBuildItem> stack0 = new Stack<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(stack0);
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn("asYelse").when(templatePathBuildItem0).getPath();
      stack0.add(templatePathBuildItem0);
      LoopSectionHelper.Factory loopSectionHelper_Factory0 = new LoopSectionHelper.Factory();
      List<String> list0 = loopSectionHelper_Factory0.getBlockLabels();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(list0).when(quteConfig0).suffixes();
      TemplateFilePathsBuildItem templateFilePathsBuildItem0 = quteProcessor0.collectTemplateFilePaths(quteConfig0, effectiveTemplatePathsBuildItem0);
      assertNotNull(templateFilePathsBuildItem0);
  }

  @Test(timeout = 4000)
  public void testDefaultTemplateRoot()  throws Throwable  {
      ResolvedDependencyBuilder resolvedDependencyBuilder0 = new ResolvedDependencyBuilder();
      EmptyPathTree emptyPathTree0 = new EmptyPathTree();
      ApplicationArchiveImpl applicationArchiveImpl0 = new ApplicationArchiveImpl((IndexView) null, emptyPathTree0, resolvedDependencyBuilder0);
      HashSet<ApplicationArchive> hashSet0 = new HashSet<ApplicationArchive>();
      ApplicationArchivesBuildItem applicationArchivesBuildItem0 = new ApplicationArchivesBuildItem(applicationArchiveImpl0, hashSet0);
      ApplicationModelBuilder applicationModelBuilder0 = new ApplicationModelBuilder();
      ApplicationModelBuilder applicationModelBuilder1 = applicationModelBuilder0.setAppArtifact(resolvedDependencyBuilder0);
      DefaultApplicationModel defaultApplicationModel0 = applicationModelBuilder1.build();
      CurateOutcomeBuildItem curateOutcomeBuildItem0 = new CurateOutcomeBuildItem(defaultApplicationModel0);
      ArrayList<TemplatePathExcludeBuildItem> arrayList0 = new ArrayList<TemplatePathExcludeBuildItem>();
      Class<HotDeploymentWatchedFileBuildItem> class0 = HotDeploymentWatchedFileBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<HotDeploymentWatchedFileBuildItem> buildProducerImpl0 = new BuildProducerImpl<HotDeploymentWatchedFileBuildItem>(class0, buildContext0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      ArrayList<TemplateRootBuildItem> arrayList1 = new ArrayList<TemplateRootBuildItem>();
      DevModeType devModeType0 = DevModeType.LOCAL;
      Optional<DevModeType> optional0 = Optional.of(devModeType0);
      LaunchMode launchMode0 = LaunchMode.TEST;
      LaunchModeBuildItem launchModeBuildItem0 = new LaunchModeBuildItem(launchMode0, optional0, true, optional0, false);
      Class<TemplatePathBuildItem> class1 = TemplatePathBuildItem.class;
      BuildContext buildContext1 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TemplatePathBuildItem> buildProducerImpl1 = new BuildProducerImpl<TemplatePathBuildItem>(class1, buildContext1);
      Class<NativeImageResourceBuildItem> class2 = NativeImageResourceBuildItem.class;
      BuildContext buildContext2 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<NativeImageResourceBuildItem> buildProducerImpl2 = new BuildProducerImpl<NativeImageResourceBuildItem>(class2, buildContext2);
      TemplateRootBuildItem templateRootBuildItem0 = quteProcessor0.defaultTemplateRoot();
      arrayList1.add(templateRootBuildItem0);
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn((Pattern) null).when(quteConfig0).templatePathExclude();
      TemplateRootsBuildItem templateRootsBuildItem0 = quteProcessor0.collectTemplateRoots(arrayList1);
      quteProcessor0.collectTemplates(applicationArchivesBuildItem0, curateOutcomeBuildItem0, arrayList0, buildProducerImpl0, buildProducerImpl1, buildProducerImpl2, quteConfig0, templateRootsBuildItem0, launchModeBuildItem0);
      assertFalse(launchModeBuildItem0.isTest());
  }

  @Test(timeout = 4000)
  public void testCollectTemplates0()  throws Throwable  {
      ResolvedDependencyBuilder resolvedDependencyBuilder0 = new ResolvedDependencyBuilder();
      EmptyPathTree emptyPathTree0 = new EmptyPathTree();
      ApplicationArchiveImpl applicationArchiveImpl0 = new ApplicationArchiveImpl((IndexView) null, emptyPathTree0, resolvedDependencyBuilder0);
      HashSet<ApplicationArchive> hashSet0 = new HashSet<ApplicationArchive>();
      ApplicationArchivesBuildItem applicationArchivesBuildItem0 = new ApplicationArchivesBuildItem(applicationArchiveImpl0, hashSet0);
      ApplicationModelBuilder applicationModelBuilder0 = new ApplicationModelBuilder();
      ApplicationModelBuilder applicationModelBuilder1 = applicationModelBuilder0.setAppArtifact(resolvedDependencyBuilder0);
      DefaultApplicationModel defaultApplicationModel0 = applicationModelBuilder1.build();
      CurateOutcomeBuildItem curateOutcomeBuildItem0 = new CurateOutcomeBuildItem(defaultApplicationModel0);
      ArrayList<TemplatePathExcludeBuildItem> arrayList0 = new ArrayList<TemplatePathExcludeBuildItem>();
      Class<HotDeploymentWatchedFileBuildItem> class0 = HotDeploymentWatchedFileBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      hashSet0.add(applicationArchiveImpl0);
      BuildProducerImpl<HotDeploymentWatchedFileBuildItem> buildProducerImpl0 = new BuildProducerImpl<HotDeploymentWatchedFileBuildItem>(class0, buildContext0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Vector<TemplateRootBuildItem> vector0 = new Vector<TemplateRootBuildItem>();
      TemplateRootsBuildItem templateRootsBuildItem0 = quteProcessor0.collectTemplateRoots(vector0);
      DevModeType devModeType0 = DevModeType.LOCAL;
      Optional<DevModeType> optional0 = Optional.of(devModeType0);
      LaunchMode launchMode0 = LaunchMode.TEST;
      LaunchModeBuildItem launchModeBuildItem0 = new LaunchModeBuildItem(launchMode0, optional0, true, optional0, true);
      Class<TemplatePathBuildItem> class1 = TemplatePathBuildItem.class;
      BuildContext buildContext1 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TemplatePathBuildItem> buildProducerImpl1 = new BuildProducerImpl<TemplatePathBuildItem>(class1, buildContext1);
      BuildProducer<NativeImageResourceBuildItem> buildProducer0 = (BuildProducer<NativeImageResourceBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      Pattern pattern0 = Pattern.compile("%5XK");
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(pattern0).when(quteConfig0).templatePathExclude();
      quteProcessor0.collectTemplates(applicationArchivesBuildItem0, curateOutcomeBuildItem0, arrayList0, buildProducerImpl0, buildProducerImpl1, buildProducer0, quteConfig0, templateRootsBuildItem0, launchModeBuildItem0);
      assertEquals(0, arrayList0.size());
  }

  @Test(timeout = 4000)
  public void testCollectTemplates1()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      File[] fileArray0 = new File[0];
      Index index0 = Index.of(fileArray0);
      DirectoryPathTree directoryPathTree0 = new DirectoryPathTree();
      ResolvedDependencyBuilder resolvedDependencyBuilder0 = new ResolvedDependencyBuilder();
      ApplicationArchiveImpl applicationArchiveImpl0 = new ApplicationArchiveImpl(index0, directoryPathTree0, resolvedDependencyBuilder0);
      HashSet<ApplicationArchive> hashSet0 = new HashSet<ApplicationArchive>();
      ApplicationArchivesBuildItem applicationArchivesBuildItem0 = new ApplicationArchivesBuildItem(applicationArchiveImpl0, hashSet0);
      ApplicationModelBuilder applicationModelBuilder0 = new ApplicationModelBuilder();
      ApplicationModelBuilder applicationModelBuilder1 = applicationModelBuilder0.setAppArtifact(resolvedDependencyBuilder0);
      DefaultApplicationModel defaultApplicationModel0 = applicationModelBuilder1.build();
      CurateOutcomeBuildItem curateOutcomeBuildItem0 = new CurateOutcomeBuildItem(defaultApplicationModel0);
      ArrayList<TemplatePathExcludeBuildItem> arrayList0 = new ArrayList<TemplatePathExcludeBuildItem>();
      Class<HotDeploymentWatchedFileBuildItem> class0 = HotDeploymentWatchedFileBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<HotDeploymentWatchedFileBuildItem> buildProducerImpl0 = new BuildProducerImpl<HotDeploymentWatchedFileBuildItem>(class0, buildContext0);
      BuildProducer<TemplatePathBuildItem> buildProducer0 = (BuildProducer<TemplatePathBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<NativeImageResourceBuildItem> buildProducer1 = (BuildProducer<NativeImageResourceBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn((Pattern) null).when(quteConfig0).templatePathExclude();
      Vector<TemplateRootBuildItem> vector0 = new Vector<TemplateRootBuildItem>();
      TemplateRootsBuildItem templateRootsBuildItem0 = quteProcessor0.collectTemplateRoots(vector0);
      LaunchMode launchMode0 = LaunchMode.DEVELOPMENT;
      DevModeType devModeType0 = DevModeType.REMOTE_LOCAL_SIDE;
      Optional<DevModeType> optional0 = Optional.of(devModeType0);
      LaunchModeBuildItem launchModeBuildItem0 = new LaunchModeBuildItem(launchMode0, optional0, true, optional0, true);
      quteProcessor0.collectTemplates(applicationArchivesBuildItem0, curateOutcomeBuildItem0, arrayList0, buildProducerImpl0, buildProducer0, buildProducer1, quteConfig0, templateRootsBuildItem0, launchModeBuildItem0);
      assertEquals(LaunchMode.DEVELOPMENT, launchModeBuildItem0.getLaunchMode());
  }

  @Test(timeout = 4000)
  public void testCollectTemplatesWithNonEmptyList()  throws Throwable  {
      ResolvedDependencyBuilder resolvedDependencyBuilder0 = new ResolvedDependencyBuilder();
      EmptyPathTree emptyPathTree0 = new EmptyPathTree();
      ApplicationArchiveImpl applicationArchiveImpl0 = new ApplicationArchiveImpl((IndexView) null, emptyPathTree0, resolvedDependencyBuilder0);
      HashSet<ApplicationArchive> hashSet0 = new HashSet<ApplicationArchive>();
      ApplicationArchivesBuildItem applicationArchivesBuildItem0 = new ApplicationArchivesBuildItem(applicationArchiveImpl0, hashSet0);
      ApplicationModelBuilder applicationModelBuilder0 = new ApplicationModelBuilder();
      ApplicationModelBuilder applicationModelBuilder1 = applicationModelBuilder0.setAppArtifact(resolvedDependencyBuilder0);
      DefaultApplicationModel defaultApplicationModel0 = applicationModelBuilder1.build();
      CurateOutcomeBuildItem curateOutcomeBuildItem0 = new CurateOutcomeBuildItem(defaultApplicationModel0);
      ArrayList<TemplatePathExcludeBuildItem> arrayList0 = new ArrayList<TemplatePathExcludeBuildItem>();
      Class<HotDeploymentWatchedFileBuildItem> class0 = HotDeploymentWatchedFileBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<HotDeploymentWatchedFileBuildItem> buildProducerImpl0 = new BuildProducerImpl<HotDeploymentWatchedFileBuildItem>(class0, buildContext0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      ArrayList<TemplateRootBuildItem> arrayList1 = new ArrayList<TemplateRootBuildItem>();
      TemplateRootsBuildItem templateRootsBuildItem0 = quteProcessor0.collectTemplateRoots(arrayList1);
      LaunchMode launchMode0 = LaunchMode.DEVELOPMENT;
      DevModeType devModeType0 = DevModeType.LOCAL;
      Optional<DevModeType> optional0 = Optional.of(devModeType0);
      LaunchModeBuildItem launchModeBuildItem0 = new LaunchModeBuildItem(launchMode0, optional0, true, optional0, true);
      TemplatePathExcludeBuildItem templatePathExcludeBuildItem0 = new TemplatePathExcludeBuildItem("%]LH");
      arrayList0.add(templatePathExcludeBuildItem0);
      Class<TemplatePathBuildItem> class1 = TemplatePathBuildItem.class;
      BuildContext buildContext1 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TemplatePathBuildItem> buildProducerImpl1 = new BuildProducerImpl<TemplatePathBuildItem>(class1, buildContext1);
      BuildProducer<NativeImageResourceBuildItem> buildProducer0 = (BuildProducer<NativeImageResourceBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      Pattern pattern0 = Pattern.compile("TNo/=Zx", 99);
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(pattern0).when(quteConfig0).templatePathExclude();
      quteProcessor0.collectTemplates(applicationArchivesBuildItem0, curateOutcomeBuildItem0, arrayList0, buildProducerImpl0, buildProducerImpl1, buildProducer0, quteConfig0, templateRootsBuildItem0, launchModeBuildItem0);
      assertTrue(launchModeBuildItem0.isAuxiliaryApplication());
  }

  @Test(timeout = 4000)
  public void testCreatesExistingValueResolversTakingNoArgumentsAndCallsAddGlobal0()  throws Throwable  {
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      DotName dotName0 = DotName.BOOLEAN_CLASS_NAME;
      AlwaysFalsePredicate<Object> alwaysFalsePredicate0 = new AlwaysFalsePredicate<Object>();
      Predicate<DotName> predicate0 = Predicate.not(alwaysFalsePredicate0);
      quteProcessor_ExistingValueResolvers0.addGlobal(dotName0, "] ", predicate0);
      assertFalse(dotName0.isInner());
  }

  @Test(timeout = 4000)
  public void testCreatesExistingValueResolversTakingNoArgumentsAndCallsAddGlobal1()  throws Throwable  {
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      DotName dotName0 = DotName.INTEGER_CLASS_NAME;
      AlwaysFalsePredicate<DotName> alwaysFalsePredicate0 = new AlwaysFalsePredicate<DotName>();
      quteProcessor_ExistingValueResolvers0.addGlobal(dotName0, "", alwaysFalsePredicate0);
      quteProcessor_ExistingValueResolvers0.addGlobal(dotName0, "", alwaysFalsePredicate0);
      assertTrue(dotName0.isComponentized());
  }

  @Test(timeout = 4000)
  public void testAdd()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      Type[] typeArray0 = new Type[0];
      ParameterizedType parameterizedType0 = ParameterizedType.create(":zeAelse", typeArray0);
      MethodInfo methodInfo0 = MethodInfo.create(classInfo0, ":zeAelse", typeArray0, (Type) parameterizedType0, (short)17908);
      AlwaysFalsePredicate<DotName> alwaysFalsePredicate0 = new AlwaysFalsePredicate<DotName>();
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      // Undeclared exception!
      try { 
        quteProcessor_ExistingValueResolvers0.add(methodInfo0, ":zeAelse", alwaysFalsePredicate0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot read the array length because \"this.exceptions\" is null
         //
         verifyException("org.jboss.jandex.MethodInternal", e);
      }
  }

  @Test(timeout = 4000)
  public void testAddAndAdd()  throws Throwable  {
      Class<TemplatesAnalysisBuildItem> class0 = TemplatesAnalysisBuildItem.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      Type[] typeArray0 = new Type[2];
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      MethodInfo methodInfo0 = MethodInfo.create(classInfo0, "8iYe8]k_xY", typeArray0, (Type) primitiveType0, (short) (-14193));
      AlwaysFalsePredicate<DotName> alwaysFalsePredicate0 = new AlwaysFalsePredicate<DotName>();
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      Predicate<DotName> predicate0 = Predicate.not(alwaysFalsePredicate0);
      quteProcessor_ExistingValueResolvers0.add(methodInfo0, ".z,Kb-r*F&FU,)^", predicate0);
      assertFalse(methodInfo0.isStaticInitializer());
  }

  @Test(timeout = 4000)
  public void testContains()  throws Throwable  {
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      Class<MockThrowable> class0 = MockThrowable.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      ClassType classType0 = ClassType.SHORT_CLASS;
      String[] stringArray0 = new String[9];
      stringArray0[0] = "33d@\"+so)gY";
      stringArray0[1] = "33d@\"+so)gY";
      stringArray0[2] = "33d@\"+so)gY";
      stringArray0[3] = "33d@\"+so)gY";
      stringArray0[4] = "33d@\"+so)gY";
      stringArray0[5] = "33d@\"+so)gY";
      stringArray0[6] = "33d@\"+so)gY";
      stringArray0[7] = "33d@\"+so)gY";
      stringArray0[8] = "33d@\"+so)gY";
      Type[] typeArray0 = new Type[3];
      typeArray0[0] = (Type) classType0;
      typeArray0[1] = (Type) classType0;
      typeArray0[2] = (Type) classType0;
      MethodInfo methodInfo0 = MethodInfo.create(classInfo0, "33d@\"+so)gY", stringArray0, typeArray0, typeArray0[1], (short)2641, (TypeVariable[]) null, typeArray0);
      boolean boolean0 = quteProcessor_ExistingValueResolvers0.contains(methodInfo0);
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testGenerateValueResolvers()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      DotName dotName0 = DotName.BYTE_CLASS_NAME;
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      Class<GeneratedClassBuildItem> class0 = GeneratedClassBuildItem.class;
      BuildProducerImpl<GeneratedClassBuildItem> buildProducerImpl0 = new BuildProducerImpl<GeneratedClassBuildItem>(class0, (BuildContext) null);
      BuildProducer<GeneratedResourceBuildItem> buildProducer0 = (BuildProducer<GeneratedResourceBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      Set<DotName> set0 = emptyIndex0.getSubpackages(dotName0);
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, set0);
      EmptyPathTree emptyPathTree0 = new EmptyPathTree();
      ApplicationArchiveImpl applicationArchiveImpl0 = new ApplicationArchiveImpl(emptyIndex0, emptyPathTree0, (ResolvedDependency) null);
      PriorityQueue<ApplicationArchive> priorityQueue0 = new PriorityQueue<ApplicationArchive>();
      ApplicationArchivesBuildItem applicationArchivesBuildItem0 = new ApplicationArchivesBuildItem(applicationArchiveImpl0, priorityQueue0);
      Stack<TemplateExtensionMethodBuildItem> stack0 = new Stack<TemplateExtensionMethodBuildItem>();
      LinkedList<ImplicitValueResolverBuildItem> linkedList0 = new LinkedList<ImplicitValueResolverBuildItem>();
      Stack<TemplatesAnalysisBuildItem.TemplateAnalysis> stack1 = new Stack<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(stack1);
      Vector<PanacheEntityClassesBuildItem> vector0 = new Vector<PanacheEntityClassesBuildItem>();
      Vector<TemplateDataBuildItem> vector1 = new Vector<TemplateDataBuildItem>();
      LinkedList<TemplateGlobalBuildItem> linkedList1 = new LinkedList<TemplateGlobalBuildItem>();
      LinkedList<IncorrectExpressionBuildItem> linkedList2 = new LinkedList<IncorrectExpressionBuildItem>();
      LiveReloadBuildItem liveReloadBuildItem0 = new LiveReloadBuildItem();
      CompletedApplicationClassPredicateBuildItem completedApplicationClassPredicateBuildItem0 = mock(CompletedApplicationClassPredicateBuildItem.class, new ViolatedAssumptionAnswer());
      BuildProducer<GeneratedValueResolverBuildItem> buildProducer1 = (BuildProducer<GeneratedValueResolverBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<ReflectiveClassBuildItem> buildProducer2 = (BuildProducer<ReflectiveClassBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<TemplateGlobalProviderBuildItem> buildProducer3 = (BuildProducer<TemplateGlobalProviderBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      quteProcessor0.generateValueResolvers(quteConfig0, buildProducerImpl0, buildProducer0, beanArchiveIndexBuildItem0, applicationArchivesBuildItem0, stack0, linkedList0, templatesAnalysisBuildItem0, vector0, vector1, linkedList1, linkedList2, liveReloadBuildItem0, completedApplicationClassPredicateBuildItem0, buildProducer1, buildProducer2, buildProducer3);
      assertEquals(0, linkedList0.size());
  }

  @Test(timeout = 4000)
  public void testGenerateValueResolversWithNull()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      Class<GeneratedClassBuildItem> class0 = GeneratedClassBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<GeneratedClassBuildItem> buildProducerImpl0 = new BuildProducerImpl<GeneratedClassBuildItem>(class0, buildContext0);
      BuildProducer<GeneratedResourceBuildItem> buildProducer0 = (BuildProducer<GeneratedResourceBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      CompositeIndex[] compositeIndexArray0 = new CompositeIndex[2];
      CompositeIndex compositeIndex0 = CompositeIndex.create((IndexView[]) compositeIndexArray0);
      TreeSet<DotName> treeSet0 = new TreeSet<DotName>();
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(compositeIndex0, compositeIndex0, treeSet0);
      LinkedList<TemplateExtensionMethodBuildItem> linkedList0 = new LinkedList<TemplateExtensionMethodBuildItem>();
      ArrayList<ImplicitValueResolverBuildItem> arrayList0 = new ArrayList<ImplicitValueResolverBuildItem>();
      Stack<TemplatesAnalysisBuildItem.TemplateAnalysis> stack0 = new Stack<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(stack0);
      Stack<PanacheEntityClassesBuildItem> stack1 = new Stack<PanacheEntityClassesBuildItem>();
      ArrayList<TemplateDataBuildItem> arrayList1 = new ArrayList<TemplateDataBuildItem>();
      Vector<TemplateGlobalBuildItem> vector0 = new Vector<TemplateGlobalBuildItem>();
      Vector<IncorrectExpressionBuildItem> vector1 = new Vector<IncorrectExpressionBuildItem>();
      LiveReloadBuildItem liveReloadBuildItem0 = new LiveReloadBuildItem();
      CompletedApplicationClassPredicateBuildItem completedApplicationClassPredicateBuildItem0 = mock(CompletedApplicationClassPredicateBuildItem.class, new ViolatedAssumptionAnswer());
      BuildProducer<GeneratedValueResolverBuildItem> buildProducer1 = (BuildProducer<GeneratedValueResolverBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<ReflectiveClassBuildItem> buildProducer2 = (BuildProducer<ReflectiveClassBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      TemplateNode.Origin templateNode_Origin0 = mock(TemplateNode.Origin.class, new ViolatedAssumptionAnswer());
      IncorrectExpressionBuildItem incorrectExpressionBuildItem0 = new IncorrectExpressionBuildItem("RegexFileFilter [pattern=", "RegexFileFilter [pattern=", "uAV&7biaiC@e7[M9", templateNode_Origin0, "RegexFileFilter [pattern=");
      vector1.add(incorrectExpressionBuildItem0);
      quteProcessor0.generateValueResolvers(quteConfig0, buildProducerImpl0, buildProducer0, beanArchiveIndexBuildItem0, (ApplicationArchivesBuildItem) null, linkedList0, arrayList0, templatesAnalysisBuildItem0, stack1, arrayList1, vector0, vector1, liveReloadBuildItem0, completedApplicationClassPredicateBuildItem0, buildProducer1, buildProducer2, (BuildProducer<TemplateGlobalProviderBuildItem>) null);
      assertTrue(arrayList0.isEmpty());
  }

  @Test(timeout = 4000)
  public void testFindTemplatePathThrowsNullPointerException()  throws Throwable  {
      LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedList0 = new LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      linkedList0.add((TemplatesAnalysisBuildItem.TemplateAnalysis) null);
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(linkedList0);
      // Undeclared exception!
      try { 
        QuteProcessor.findTemplatePath(templatesAnalysisBuildItem0, "~NO@}0E:x]MO!rYs|");
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot read field \"generatedId\" because \"templateAnalysis\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testEnumConstantFilterReturningTrue()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      FieldInfo fieldInfo0 = FieldInfo.create(classInfo0, "eJ-.+dTm@]T8'", primitiveType0, (short)26132);
      boolean boolean0 = QuteProcessor.enumConstantFilter(fieldInfo0);
      assertTrue(boolean0);
  }

  @Test(timeout = 4000)
  public void testEnumConstantFilterReturningFalse()  throws Throwable  {
      Class<String> class0 = String.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      boolean boolean0 = QuteProcessor.enumConstantFilter(classInfo0);
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testStaticsFilterThrowsIllegalArgumentException()  throws Throwable  {
      Class<ResolvedDependencyBuilder> class0 = ResolvedDependencyBuilder.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      // Undeclared exception!
      try { 
        QuteProcessor.staticsFilter(classInfo0);
        fail("Expecting exception: IllegalArgumentException");
      
      } catch(IllegalArgumentException e) {
         //
         // no message in exception (getMessage() returned null)
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testStaticsFilterReturningFalse()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      FieldInfo fieldInfo0 = FieldInfo.create(classInfo0, "eJ-.+dTm@]T8'", primitiveType0, (short)26132);
      boolean boolean0 = QuteProcessor.staticsFilter(fieldInfo0);
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testStaticsFilterReturningTrue()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      Type[] typeArray0 = new Type[1];
      ParameterizedType parameterizedType0 = ParameterizedType.create("E26`V_U", typeArray0);
      MethodInfo methodInfo0 = MethodInfo.create(classInfo0, "E26`V_U", typeArray0, (Type) parameterizedType0, (short)17881);
      boolean boolean0 = QuteProcessor.staticsFilter(methodInfo0);
      assertTrue(boolean0);
  }

  @Test(timeout = 4000)
  public void testDefaultFilter0()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      FieldInfo fieldInfo0 = FieldInfo.create(classInfo0, "", primitiveType0, (short)26132);
      boolean boolean0 = QuteProcessor.defaultFilter(fieldInfo0);
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testDefaultFilter1()  throws Throwable  {
      Class<String> class0 = String.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      Type[] typeArray0 = new Type[18];
      ParameterizedType parameterizedType0 = ParameterizedType.create("IYO[", typeArray0);
      FieldInfo fieldInfo0 = FieldInfo.create(classInfo0, "IYO[", parameterizedType0, (short) (-3709));
      boolean boolean0 = QuteProcessor.defaultFilter(fieldInfo0);
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testDefaultFilterThrowsIllegalArgumentException()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      // Undeclared exception!
      try { 
        QuteProcessor.defaultFilter(classInfo0);
        fail("Expecting exception: IllegalArgumentException");
      
      } catch(IllegalArgumentException e) {
         //
         // no message in exception (getMessage() returned null)
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testDefaultFilterReturningTrue()  throws Throwable  {
      Class<TemplatesAnalysisBuildItem> class0 = TemplatesAnalysisBuildItem.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      Type[] typeArray0 = new Type[2];
      PrimitiveType primitiveType0 = PrimitiveType.BYTE;
      MethodInfo methodInfo0 = MethodInfo.create(classInfo0, "8iYe8]k_xY", typeArray0, (Type) primitiveType0, (short) (-14193));
      boolean boolean0 = QuteProcessor.defaultFilter(methodInfo0);
      assertTrue(boolean0);
  }

  @Test(timeout = 4000)
  public void testCollectTemplateExtensionMethods()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      IndexView indexView0 = IndexView.empty();
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(indexView0, indexView0, (Set<DotName>) null);
      Class<TemplateExtensionMethodBuildItem> class0 = TemplateExtensionMethodBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TemplateExtensionMethodBuildItem> buildProducerImpl0 = new BuildProducerImpl<TemplateExtensionMethodBuildItem>(class0, buildContext0);
      quteProcessor0.collectTemplateExtensionMethods(beanArchiveIndexBuildItem0, buildProducerImpl0);
  }

  @Test(timeout = 4000)
  public void testBuildIgnorePattern()  throws Throwable  {
      SetSectionHelper.Factory setSectionHelper_Factory0 = new SetSectionHelper.Factory();
      List<String> list0 = setSectionHelper_Factory0.getDefaultAliases();
      String string0 = QuteProcessor.buildIgnorePattern(list0);
      assertEquals("^(?!\\Qset\\E|\\Qlet\\E).*$", string0);
  }

  @Test(timeout = 4000)
  public void testBuildIgnorePatternThrowsIllegalArgumentException()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Vector<TemplateRootBuildItem> vector0 = new Vector<TemplateRootBuildItem>();
      TemplateRootsBuildItem templateRootsBuildItem0 = quteProcessor0.collectTemplateRoots(vector0);
      // Undeclared exception!
      try { 
        QuteProcessor.buildIgnorePattern(templateRootsBuildItem0);
        fail("Expecting exception: IllegalArgumentException");
      
      } catch(IllegalArgumentException e) {
         //
         // no message in exception (getMessage() returned null)
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testRegisterRenderedResultsReturningNonNull()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteTestModeConfig quteTestModeConfig0 = mock(QuteTestModeConfig.class, new ViolatedAssumptionAnswer());
      doReturn(true).when(quteTestModeConfig0).recordRenderedResults();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(quteTestModeConfig0).when(quteConfig0).testMode();
      SyntheticBeanBuildItem syntheticBeanBuildItem0 = quteProcessor0.registerRenderedResults(quteConfig0);
      assertNotNull(syntheticBeanBuildItem0);
  }

  @Test(timeout = 4000)
  public void testRegisterRenderedResultsReturningNull()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteTestModeConfig quteTestModeConfig0 = mock(QuteTestModeConfig.class, new ViolatedAssumptionAnswer());
      doReturn(false).when(quteTestModeConfig0).recordRenderedResults();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(quteTestModeConfig0).when(quteConfig0).testMode();
      SyntheticBeanBuildItem syntheticBeanBuildItem0 = quteProcessor0.registerRenderedResults(quteConfig0);
      assertNull(syntheticBeanBuildItem0);
  }

  @Test(timeout = 4000)
  public void testValidateCheckedFragmentsThrowsNullPointerException0()  throws Throwable  {
      Stack<Expression> stack0 = new Stack<Expression>();
      CheckedFragmentValidationBuildItem checkedFragmentValidationBuildItem0 = new CheckedFragmentValidationBuildItem("$b\"Dl)<fi:L d}|I8<", stack0, (CheckedTemplateBuildItem) null);
      List<CheckedFragmentValidationBuildItem> list0 = List.of(checkedFragmentValidationBuildItem0, checkedFragmentValidationBuildItem0);
      HashMap<Integer, QuteProcessor.MatchResult> hashMap0 = new HashMap<Integer, QuteProcessor.MatchResult>();
      TemplateExpressionMatchesBuildItem templateExpressionMatchesBuildItem0 = new TemplateExpressionMatchesBuildItem("$b\"Dl)<fi:L d}|I8<", hashMap0);
      List<TemplateExpressionMatchesBuildItem> list1 = List.of(templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0);
      ArrayList<TemplateGlobalBuildItem> arrayList0 = new ArrayList<TemplateGlobalBuildItem>();
      stack0.add((Expression) null);
      LinkedHashSet<DotName> linkedHashSet0 = new LinkedHashSet<DotName>();
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, linkedHashSet0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<ValidationPhaseBuildItem.ValidationErrorBuildItem> class0 = ValidationPhaseBuildItem.ValidationErrorBuildItem.class;
      BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem> buildProducerImpl0 = new BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem>(class0, (BuildContext) null);
      // Undeclared exception!
      try { 
        quteProcessor0.validateCheckedFragments(list0, list1, arrayList0, beanArchiveIndexBuildItem0, buildProducerImpl0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.qute.Expression.isLiteral()\" because \"expression\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testValidateCheckedFragmentsThrowsNullPointerException1()  throws Throwable  {
      Vector<Expression> vector0 = new Vector<Expression>();
      CheckedFragmentValidationBuildItem checkedFragmentValidationBuildItem0 = new CheckedFragmentValidationBuildItem("$DCQ", vector0, (CheckedTemplateBuildItem) null);
      List<CheckedFragmentValidationBuildItem> list0 = List.of(checkedFragmentValidationBuildItem0, checkedFragmentValidationBuildItem0);
      HashMap<Integer, QuteProcessor.MatchResult> hashMap0 = new HashMap<Integer, QuteProcessor.MatchResult>();
      TemplateExpressionMatchesBuildItem templateExpressionMatchesBuildItem0 = new TemplateExpressionMatchesBuildItem("count", hashMap0);
      List<TemplateExpressionMatchesBuildItem> list1 = List.of(templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0);
      ArrayList<TemplateGlobalBuildItem> arrayList0 = new ArrayList<TemplateGlobalBuildItem>();
      LinkedHashSet<DotName> linkedHashSet0 = new LinkedHashSet<DotName>();
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, linkedHashSet0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<ValidationPhaseBuildItem.ValidationErrorBuildItem> class0 = ValidationPhaseBuildItem.ValidationErrorBuildItem.class;
      BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem> buildProducerImpl0 = new BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem>(class0, (BuildContext) null);
      // Undeclared exception!
      try { 
        quteProcessor0.validateCheckedFragments(list0, list1, arrayList0, beanArchiveIndexBuildItem0, buildProducerImpl0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot read field \"templateId\" because \"validation.checkedTemplate\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testValidateCheckedFragments()  throws Throwable  {
      Stack<Expression> stack0 = new Stack<Expression>();
      CheckedFragmentValidationBuildItem checkedFragmentValidationBuildItem0 = new CheckedFragmentValidationBuildItem("$b\"Dl)<fi:L d}|I8<", stack0, (CheckedTemplateBuildItem) null);
      List<CheckedFragmentValidationBuildItem> list0 = List.of(checkedFragmentValidationBuildItem0, checkedFragmentValidationBuildItem0);
      HashMap<Integer, QuteProcessor.MatchResult> hashMap0 = new HashMap<Integer, QuteProcessor.MatchResult>();
      TemplateExpressionMatchesBuildItem templateExpressionMatchesBuildItem0 = new TemplateExpressionMatchesBuildItem("$b\"Dl)<fi:L d}|I8<", hashMap0);
      List<TemplateExpressionMatchesBuildItem> list1 = List.of(templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0, templateExpressionMatchesBuildItem0);
      ArrayList<TemplateGlobalBuildItem> arrayList0 = new ArrayList<TemplateGlobalBuildItem>();
      LinkedHashSet<DotName> linkedHashSet0 = new LinkedHashSet<DotName>();
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, linkedHashSet0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<ValidationPhaseBuildItem.ValidationErrorBuildItem> class0 = ValidationPhaseBuildItem.ValidationErrorBuildItem.class;
      BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem> buildProducerImpl0 = new BuildProducerImpl<ValidationPhaseBuildItem.ValidationErrorBuildItem>(class0, (BuildContext) null);
      quteProcessor0.validateCheckedFragments(list0, list1, arrayList0, beanArchiveIndexBuildItem0, buildProducerImpl0);
      assertEquals(10, list1.size());
  }

  @Test(timeout = 4000)
  public void testValidateCheckedFragmentsWithNull()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Stack<CheckedFragmentValidationBuildItem> stack0 = new Stack<CheckedFragmentValidationBuildItem>();
      Stack<TemplateExpressionMatchesBuildItem> stack1 = new Stack<TemplateExpressionMatchesBuildItem>();
      ArrayList<TemplateGlobalBuildItem> arrayList0 = new ArrayList<TemplateGlobalBuildItem>();
      quteProcessor0.validateCheckedFragments(stack0, stack1, arrayList0, (BeanArchiveIndexBuildItem) null, (BuildProducer<ValidationPhaseBuildItem.ValidationErrorBuildItem>) null);
      assertTrue(arrayList0.isEmpty());
  }

  @Test(timeout = 4000)
  public void testAnalyzeTemplatesWithNonEmptyList()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      ArrayList<TemplatePathBuildItem> arrayList0 = new ArrayList<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(arrayList0);
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      TemplateFilePathsBuildItem templateFilePathsBuildItem0 = quteProcessor0.collectTemplateFilePaths(quteConfig0, effectiveTemplatePathsBuildItem0);
      Stack<TemplateGlobalBuildItem> stack0 = new Stack<TemplateGlobalBuildItem>();
      ArrayList<ValidationParserHookBuildItem> arrayList1 = new ArrayList<ValidationParserHookBuildItem>();
      LinkedBlockingQueue<ClassInfo> linkedBlockingQueue0 = new LinkedBlockingQueue<ClassInfo>();
      EngineConfigurationsBuildItem engineConfigurationsBuildItem0 = new EngineConfigurationsBuildItem(linkedBlockingQueue0);
      Optional<EngineConfigurationsBuildItem> optional0 = Optional.of(engineConfigurationsBuildItem0);
      Stack<MessageBundleMethodBuildItem> stack1 = new Stack<MessageBundleMethodBuildItem>();
      ArrayList<CheckedTemplateBuildItem> arrayList2 = new ArrayList<CheckedTemplateBuildItem>();
      QuteConfig quteConfig1 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      Stack<IndexView> stack2 = new Stack<IndexView>();
      StackedIndex stackedIndex0 = StackedIndex.create((List<IndexView>) stack2);
      MessageBundleMethodBuildItem messageBundleMethodBuildItem0 = new MessageBundleMethodBuildItem((String) null, (String) null, "Dp!7Q nUn'", (MethodInfo) null, "Dp!7Q nUn'", true, true);
      stack1.add(messageBundleMethodBuildItem0);
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(stackedIndex0, stackedIndex0, (Set<DotName>) null);
      Class<CheckedFragmentValidationBuildItem> class0 = CheckedFragmentValidationBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<CheckedFragmentValidationBuildItem> buildProducerImpl0 = new BuildProducerImpl<CheckedFragmentValidationBuildItem>(class0, buildContext0);
      BuildProducer<TemplatesAnalysisBuildItem> buildProducer0 = (BuildProducer<TemplatesAnalysisBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      quteProcessor0.analyzeTemplates(effectiveTemplatePathsBuildItem0, templateFilePathsBuildItem0, arrayList2, stack1, stack0, quteConfig1, arrayList1, optional0, beanArchiveIndexBuildItem0, buildProducerImpl0, buildProducer0);
      assertEquals(10, stack1.capacity());
  }

  @Test(timeout = 4000)
  public void testAnalyzeTemplatesThrowsNullPointerExceptionAndCollectTemplateFilePaths()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Stack<TemplatePathBuildItem> stack0 = new Stack<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(stack0);
      Vector<String> vector0 = new Vector<String>();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(vector0).when(quteConfig0).suffixes();
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn("uAV&7biaiC@e7[M9").when(templatePathBuildItem0).getContent();
      doReturn("uAV&7biaiC@e7[M9", "zC:wEVSFxeubc", "zC:wEVSFxeubc", "zC:wEVSFxeubc", "zC:wEVSFxeubc").when(templatePathBuildItem0).getPath();
      doReturn(true).when(templatePathBuildItem0).isTag();
      stack0.add(templatePathBuildItem0);
      QuteConfig quteConfig1 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(vector0).when(quteConfig1).suffixes();
      quteProcessor0.collectTemplateVariants(effectiveTemplatePathsBuildItem0, quteConfig1);
      QuteConfig quteConfig2 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(vector0).when(quteConfig2).suffixes();
      TemplateFilePathsBuildItem templateFilePathsBuildItem0 = quteProcessor0.collectTemplateFilePaths(quteConfig2, effectiveTemplatePathsBuildItem0);
      LinkedList<CheckedTemplateBuildItem> linkedList0 = new LinkedList<CheckedTemplateBuildItem>();
      Vector<MessageBundleMethodBuildItem> vector1 = new Vector<MessageBundleMethodBuildItem>();
      Vector<TemplateGlobalBuildItem> vector2 = new Vector<TemplateGlobalBuildItem>();
      Vector<ValidationParserHookBuildItem> vector3 = new Vector<ValidationParserHookBuildItem>();
      Optional<EngineConfigurationsBuildItem> optional0 = Optional.empty();
      Class<CheckedFragmentValidationBuildItem> class0 = CheckedFragmentValidationBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      vector3.add((ValidationParserHookBuildItem) null);
      BuildProducerImpl<CheckedFragmentValidationBuildItem> buildProducerImpl0 = new BuildProducerImpl<CheckedFragmentValidationBuildItem>(class0, buildContext0);
      BuildProducer<TemplatesAnalysisBuildItem> buildProducer0 = (BuildProducer<TemplatesAnalysisBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      // Undeclared exception!
      try { 
        quteProcessor0.analyzeTemplates(effectiveTemplatePathsBuildItem0, templateFilePathsBuildItem0, linkedList0, vector1, vector2, quteConfig0, vector3, optional0, (BeanArchiveIndexBuildItem) null, buildProducerImpl0, buildProducer0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.qute.deployment.ValidationParserHookBuildItem.accept(io.quarkus.qute.ParserHelper)\" because \"hook\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor$5", e);
      }
  }

  @Test(timeout = 4000)
  public void testAnalyzeTemplates()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Vector<TemplatePathBuildItem> vector0 = new Vector<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(vector0);
      LoopSectionHelper.Factory loopSectionHelper_Factory0 = new LoopSectionHelper.Factory("V%&K7G&iiyCA#`@");
      List<String> list0 = loopSectionHelper_Factory0.getDefaultAliases();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(list0, list0).when(quteConfig0).suffixes();
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn("V%&K7G&iiyCA#`@").when(templatePathBuildItem0).getContent();
      doReturn("V%&K7G&iiyCA#`@", "zC7wEVFxeubc", "zC7wEVFxeubc", "zC7wEVFxeubc", "zC7wEVFxeubc").when(templatePathBuildItem0).getPath();
      doReturn(true).when(templatePathBuildItem0).isTag();
      vector0.add(templatePathBuildItem0);
      QuteConfig quteConfig1 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(list0).when(quteConfig1).suffixes();
      quteProcessor0.collectTemplateVariants(effectiveTemplatePathsBuildItem0, quteConfig1);
      QuteConfig quteConfig2 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(list0).when(quteConfig2).suffixes();
      TemplateFilePathsBuildItem templateFilePathsBuildItem0 = quteProcessor0.collectTemplateFilePaths(quteConfig2, effectiveTemplatePathsBuildItem0);
      LinkedList<CheckedTemplateBuildItem> linkedList0 = new LinkedList<CheckedTemplateBuildItem>();
      Vector<MessageBundleMethodBuildItem> vector1 = new Vector<MessageBundleMethodBuildItem>();
      Vector<TemplateGlobalBuildItem> vector2 = new Vector<TemplateGlobalBuildItem>();
      Vector<ValidationParserHookBuildItem> vector3 = new Vector<ValidationParserHookBuildItem>();
      Optional<EngineConfigurationsBuildItem> optional0 = Optional.empty();
      Class<CheckedFragmentValidationBuildItem> class0 = CheckedFragmentValidationBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<CheckedFragmentValidationBuildItem> buildProducerImpl0 = new BuildProducerImpl<CheckedFragmentValidationBuildItem>(class0, buildContext0);
      BuildProducer<TemplatesAnalysisBuildItem> buildProducer0 = (BuildProducer<TemplatesAnalysisBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      quteProcessor0.analyzeTemplates(effectiveTemplatePathsBuildItem0, templateFilePathsBuildItem0, linkedList0, vector1, vector2, quteConfig0, vector3, optional0, (BeanArchiveIndexBuildItem) null, buildProducerImpl0, buildProducer0);
      assertEquals(10, vector3.capacity());
  }

  @Test(timeout = 4000)
  public void testAnalyzeTemplatesThrowsNullPointerException()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Vector<TemplatePathBuildItem> vector0 = new Vector<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(vector0);
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn((String) null).when(templatePathBuildItem0).getPath();
      doReturn(false).when(templatePathBuildItem0).isTag();
      vector0.add(templatePathBuildItem0);
      Class<CheckedFragmentValidationBuildItem> class0 = CheckedFragmentValidationBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<CheckedFragmentValidationBuildItem> buildProducerImpl0 = new BuildProducerImpl<CheckedFragmentValidationBuildItem>(class0, buildContext0);
      LinkedList<TemplateGlobalBuildItem> linkedList0 = new LinkedList<TemplateGlobalBuildItem>();
      Stack<ValidationParserHookBuildItem> stack0 = new Stack<ValidationParserHookBuildItem>();
      Class<TemplatesAnalysisBuildItem> class1 = TemplatesAnalysisBuildItem.class;
      BuildContext buildContext1 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<TemplatesAnalysisBuildItem> buildProducerImpl1 = new BuildProducerImpl<TemplatesAnalysisBuildItem>(class1, buildContext1);
      LinkedList<CheckedTemplateBuildItem> linkedList1 = new LinkedList<CheckedTemplateBuildItem>();
      Stack<MessageBundleMethodBuildItem> stack1 = new Stack<MessageBundleMethodBuildItem>();
      Optional<EngineConfigurationsBuildItem> optional0 = Optional.empty();
      // Undeclared exception!
      try { 
        quteProcessor0.analyzeTemplates(effectiveTemplatePathsBuildItem0, (TemplateFilePathsBuildItem) null, linkedList1, stack1, linkedList0, (QuteConfig) null, stack0, optional0, (BeanArchiveIndexBuildItem) null, buildProducerImpl0, buildProducerImpl1);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // no message in exception (getMessage() returned null)
         //
         verifyException("java.util.concurrent.ConcurrentHashMap", e);
      }
  }

  @Test(timeout = 4000)
  public void testAnalyzeTemplatesWithNonNull()  throws Throwable  {
      Class<Object> class0 = Object.class;
      ClassInfo classInfo0 = Index.singleClass(class0);
      LinkedBlockingQueue<ClassInfo> linkedBlockingQueue0 = new LinkedBlockingQueue<ClassInfo>();
      linkedBlockingQueue0.add(classInfo0);
      EngineConfigurationsBuildItem engineConfigurationsBuildItem0 = new EngineConfigurationsBuildItem(linkedBlockingQueue0);
      Optional<EngineConfigurationsBuildItem> optional0 = Optional.of(engineConfigurationsBuildItem0);
      Stack<MessageBundleMethodBuildItem> stack0 = new Stack<MessageBundleMethodBuildItem>();
      ArrayList<CheckedTemplateBuildItem> arrayList0 = new ArrayList<CheckedTemplateBuildItem>();
      Stack<IndexView> stack1 = new Stack<IndexView>();
      StackedIndex stackedIndex0 = StackedIndex.create((List<IndexView>) stack1);
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(stackedIndex0, stackedIndex0, (Set<DotName>) null);
      Class<CheckedFragmentValidationBuildItem> class1 = CheckedFragmentValidationBuildItem.class;
      BuildProducerImpl<CheckedFragmentValidationBuildItem> buildProducerImpl0 = new BuildProducerImpl<CheckedFragmentValidationBuildItem>(class1, (BuildContext) null);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      LinkedList<TemplatePathBuildItem> linkedList0 = new LinkedList<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(linkedList0);
      LinkedList<TemplateGlobalBuildItem> linkedList1 = new LinkedList<TemplateGlobalBuildItem>();
      Vector<ValidationParserHookBuildItem> vector0 = new Vector<ValidationParserHookBuildItem>();
      Class<TemplatesAnalysisBuildItem> class2 = TemplatesAnalysisBuildItem.class;
      BuildProducerImpl<TemplatesAnalysisBuildItem> buildProducerImpl1 = new BuildProducerImpl<TemplatesAnalysisBuildItem>(class2, (BuildContext) null);
      // Undeclared exception!
      try { 
        quteProcessor0.analyzeTemplates(effectiveTemplatePathsBuildItem0, (TemplateFilePathsBuildItem) null, arrayList0, stack0, linkedList1, (QuteConfig) null, vector0, optional0, beanArchiveIndexBuildItem0, buildProducerImpl0, buildProducerImpl1);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.builder.BuildContext.produce(java.lang.Class, io.quarkus.builder.item.BuildItem)\" because \"this.buildContext\" is null
         //
         verifyException("io.quarkus.deployment.BuildProducerImpl", e);
      }
  }

  @Test(timeout = 4000)
  public void testAnalyzeTemplatesWithNullAndNull()  throws Throwable  {
      LinkedList<TemplatePathBuildItem> linkedList0 = new LinkedList<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = new EffectiveTemplatePathsBuildItem(linkedList0);
      TemplatePathBuildItem templatePathBuildItem0 = mock(TemplatePathBuildItem.class, new ViolatedAssumptionAnswer());
      doReturn("wTOsk.3").when(templatePathBuildItem0).getPath();
      doReturn(true).when(templatePathBuildItem0).isTag();
      linkedList0.add(templatePathBuildItem0);
      TemplateFilePathsBuildItem templateFilePathsBuildItem0 = new TemplateFilePathsBuildItem((Set<String>) null);
      Vector<CheckedTemplateBuildItem> vector0 = new Vector<CheckedTemplateBuildItem>();
      Stack<MessageBundleMethodBuildItem> stack0 = new Stack<MessageBundleMethodBuildItem>();
      Stack<TemplateGlobalBuildItem> stack1 = new Stack<TemplateGlobalBuildItem>();
      Stack<ValidationParserHookBuildItem> stack2 = new Stack<ValidationParserHookBuildItem>();
      Class<CheckedFragmentValidationBuildItem> class0 = CheckedFragmentValidationBuildItem.class;
      BuildProducerImpl<CheckedFragmentValidationBuildItem> buildProducerImpl0 = new BuildProducerImpl<CheckedFragmentValidationBuildItem>(class0, (BuildContext) null);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      // Undeclared exception!
      try { 
        quteProcessor0.analyzeTemplates(effectiveTemplatePathsBuildItem0, templateFilePathsBuildItem0, vector0, stack0, stack1, quteConfig0, stack2, (Optional<EngineConfigurationsBuildItem>) null, (BeanArchiveIndexBuildItem) null, buildProducerImpl0, (BuildProducer<TemplatesAnalysisBuildItem>) null);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"java.util.Optional.isPresent()\" because \"engineConfigurations\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollectEffectiveTemplatePaths()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteConfig.DuplicitTemplatesStrategy quteConfig_DuplicitTemplatesStrategy0 = QuteConfig.DuplicitTemplatesStrategy.FAIL;
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(quteConfig_DuplicitTemplatesStrategy0).when(quteConfig0).duplicitTemplatesStrategy();
      ArrayList<TemplatePathBuildItem> arrayList0 = new ArrayList<TemplatePathBuildItem>();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = quteProcessor0.collectEffectiveTemplatePaths(quteConfig0, arrayList0);
      assertNotNull(effectiveTemplatePathsBuildItem0);
  }

  @Test(timeout = 4000)
  public void testCollectCheckedTemplatesWithNull()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, (Set<DotName>) null);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Class<BytecodeTransformerBuildItem> class0 = BytecodeTransformerBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<BytecodeTransformerBuildItem> buildProducerImpl0 = new BuildProducerImpl<BytecodeTransformerBuildItem>(class0, buildContext0);
      Vector<CheckedTemplateAdapterBuildItem> vector0 = new Vector<CheckedTemplateAdapterBuildItem>();
      CheckedTemplateAdapter checkedTemplateAdapter0 = mock(CheckedTemplateAdapter.class, new ViolatedAssumptionAnswer());
      doReturn("max_micro_jump").when(checkedTemplateAdapter0).templateInstanceBinaryName();
      CheckedTemplateAdapterBuildItem checkedTemplateAdapterBuildItem0 = new CheckedTemplateAdapterBuildItem(checkedTemplateAdapter0);
      vector0.add(checkedTemplateAdapterBuildItem0);
      CustomTemplateLocatorPatternsBuildItem customTemplateLocatorPatternsBuildItem0 = quteProcessor0.validateAndCollectCustomTemplateLocatorLocations(beanArchiveIndexBuildItem0, (BuildProducer<ValidationPhaseBuildItem.ValidationErrorBuildItem>) null);
      // Undeclared exception!
      try { 
        quteProcessor0.collectCheckedTemplates(beanArchiveIndexBuildItem0, buildProducerImpl0, vector0, (TemplateFilePathsBuildItem) null, customTemplateLocatorPatternsBuildItem0);
        fail("Expecting exception: IllegalStateException");
      
      } catch(IllegalStateException e) {
         //
         // io.quarkus.qute.TemplateInstance not found in the index
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testCollectCheckedTemplatesWithNonNull()  throws Throwable  {
      ArrayList<TemplatePathBuildItem> arrayList0 = new ArrayList<TemplatePathBuildItem>();
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QuteConfig.DuplicitTemplatesStrategy quteConfig_DuplicitTemplatesStrategy0 = QuteConfig.DuplicitTemplatesStrategy.PRIORITIZE;
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      doReturn(quteConfig_DuplicitTemplatesStrategy0).when(quteConfig0).duplicitTemplatesStrategy();
      EffectiveTemplatePathsBuildItem effectiveTemplatePathsBuildItem0 = quteProcessor0.collectEffectiveTemplatePaths(quteConfig0, arrayList0);
      QuteConfig quteConfig1 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      TemplateFilePathsBuildItem templateFilePathsBuildItem0 = quteProcessor0.collectTemplateFilePaths(quteConfig1, effectiveTemplatePathsBuildItem0);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      LinkedHashSet<DotName> linkedHashSet0 = new LinkedHashSet<DotName>();
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, linkedHashSet0);
      Class<BytecodeTransformerBuildItem> class0 = BytecodeTransformerBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<BytecodeTransformerBuildItem> buildProducerImpl0 = new BuildProducerImpl<BytecodeTransformerBuildItem>(class0, buildContext0);
      ArrayList<CheckedTemplateAdapterBuildItem> arrayList1 = new ArrayList<CheckedTemplateAdapterBuildItem>();
      BuildProducer<ValidationPhaseBuildItem.ValidationErrorBuildItem> buildProducer0 = (BuildProducer<ValidationPhaseBuildItem.ValidationErrorBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      CustomTemplateLocatorPatternsBuildItem customTemplateLocatorPatternsBuildItem0 = quteProcessor0.validateAndCollectCustomTemplateLocatorLocations(beanArchiveIndexBuildItem0, buildProducer0);
      // Undeclared exception!
      try { 
        quteProcessor0.collectCheckedTemplates(beanArchiveIndexBuildItem0, buildProducerImpl0, arrayList1, templateFilePathsBuildItem0, customTemplateLocatorPatternsBuildItem0);
        fail("Expecting exception: IllegalStateException");
      
      } catch(IllegalStateException e) {
         //
         // io.quarkus.qute.TemplateInstance not found in the index
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessTemplateErrorsThrowsRuntimeException1()  throws Throwable  {
      LinkedList<IncorrectExpressionBuildItem> linkedList0 = new LinkedList<IncorrectExpressionBuildItem>();
      TemplateNode.Origin templateNode_Origin0 = mock(TemplateNode.Origin.class, new ViolatedAssumptionAnswer());
      doReturn(0).when(templateNode_Origin0).getLine();
      doReturn(0).when(templateNode_Origin0).getLineCharacterStart();
      doReturn((String) null).when(templateNode_Origin0).getTemplateGeneratedId();
      doReturn((String) null).when(templateNode_Origin0).getTemplateId();
      IncorrectExpressionBuildItem incorrectExpressionBuildItem0 = new IncorrectExpressionBuildItem("", "vhYwb/Mvnk", templateNode_Origin0);
      linkedList0.add(incorrectExpressionBuildItem0);
      QuteProcessor quteProcessor0 = new QuteProcessor();
      LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedList1 = new LinkedList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(linkedList1);
      // Undeclared exception!
      try { 
        quteProcessor0.processTemplateErrors(templatesAnalysisBuildItem0, linkedList0, (BuildProducer<ServiceStartBuildItem>) null);
        fail("Expecting exception: RuntimeException");
      
      } catch(RuntimeException e) {
         //
         // Found incorrect expressions (1):
         // \t[1] :java.lang.Integer@0000000121:java.lang.Integer@0000000121 - {java.lang.String@0000000122}: java.lang.String@0000000123
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessTemplateErrorsThrowsRuntimeException2()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      LinkedTransferQueue<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedTransferQueue0 = new LinkedTransferQueue<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis> arrayList0 = new ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis>(linkedTransferQueue0);
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(arrayList0);
      LinkedList<IncorrectExpressionBuildItem> linkedList0 = new LinkedList<IncorrectExpressionBuildItem>();
      TemplateNode.Origin templateNode_Origin0 = mock(TemplateNode.Origin.class, new ViolatedAssumptionAnswer());
      doReturn(0).when(templateNode_Origin0).getLine();
      doReturn(0).when(templateNode_Origin0).getLineCharacterStart();
      doReturn((String) null).when(templateNode_Origin0).getTemplateGeneratedId();
      doReturn((String) null).when(templateNode_Origin0).getTemplateId();
      IncorrectExpressionBuildItem incorrectExpressionBuildItem0 = new IncorrectExpressionBuildItem("", "DOCKER_HOST", "DOCKER_HOST", templateNode_Origin0);
      linkedList0.add(incorrectExpressionBuildItem0);
      // Undeclared exception!
      try { 
        quteProcessor0.processTemplateErrors(templatesAnalysisBuildItem0, linkedList0, (BuildProducer<ServiceStartBuildItem>) null);
        fail("Expecting exception: RuntimeException");
      
      } catch(RuntimeException e) {
         //
         // Found incorrect expressions (1):
         // \t[1] :java.lang.Integer@0000000137:java.lang.Integer@0000000137 - {java.lang.String@0000000138}: Property/method [java.lang.String@0000000139] not found on class [java.lang.String@0000000139] nor handled by an extension method
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testProcessTemplateErrors()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      LinkedTransferQueue<TemplatesAnalysisBuildItem.TemplateAnalysis> linkedTransferQueue0 = new LinkedTransferQueue<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis> arrayList0 = new ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis>(linkedTransferQueue0);
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(arrayList0);
      LinkedList<IncorrectExpressionBuildItem> linkedList0 = new LinkedList<IncorrectExpressionBuildItem>();
      quteProcessor0.processTemplateErrors(templatesAnalysisBuildItem0, linkedList0, (BuildProducer<ServiceStartBuildItem>) null);
      assertEquals(0, linkedList0.size());
  }

  @Test(timeout = 4000)
  public void testIndex()  throws Throwable  {
      CompositeIndex[] compositeIndexArray0 = new CompositeIndex[7];
      Predicate<Object> predicate0 = Predicate.isEqual(null);
      Predicate<AnnotationTarget> predicate1 = Predicate.not(predicate0);
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(compositeIndexArray0[5], predicate1, true);
      Boolean boolean0 = Boolean.FALSE;
      QuteProcessor.FirstPassJavaMemberLookupConfig quteProcessor_FirstPassJavaMemberLookupConfig0 = new QuteProcessor.FirstPassJavaMemberLookupConfig(quteProcessor_FixedJavaMemberLookupConfig0, predicate1, boolean0);
      quteProcessor_FirstPassJavaMemberLookupConfig0.index();
      assertTrue(quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly());
  }

  @Test(timeout = 4000)
  public void testCreatesFirstPassJavaMemberLookupConfigTaking3ArgumentsAndCallsFilter()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(emptyIndex0, (Predicate<AnnotationTarget>) null, true);
      Boolean boolean0 = Boolean.valueOf("java.lang");
      QuteProcessor.FirstPassJavaMemberLookupConfig quteProcessor_FirstPassJavaMemberLookupConfig0 = new QuteProcessor.FirstPassJavaMemberLookupConfig(quteProcessor_FixedJavaMemberLookupConfig0, (Predicate<AnnotationTarget>) null, boolean0);
      quteProcessor_FirstPassJavaMemberLookupConfig0.filter();
      assertTrue(quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly());
  }

  @Test(timeout = 4000)
  public void testDeclaredMembersOnlyAndDeclaredMembersOnly()  throws Throwable  {
      IndexView[] indexViewArray0 = new IndexView[0];
      CompositeIndex compositeIndex0 = CompositeIndex.create(indexViewArray0);
      Predicate<AnnotationTarget> predicate0 = Predicate.isEqual(compositeIndex0);
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(compositeIndex0, predicate0, false);
      boolean boolean0 = quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly();
      assertFalse(boolean0);
  }

  @Test(timeout = 4000)
  public void testNextPartAndInitDefaultMembersFilterAndCreatesFixedJavaMemberLookupConfigTaking3Arguments()  throws Throwable  {
      IndexView[] indexViewArray0 = new IndexView[0];
      CompositeIndex compositeIndex0 = CompositeIndex.create(indexViewArray0);
      Predicate<AnnotationTarget> predicate0 = QuteProcessor.initDefaultMembersFilter();
      QuteProcessor.FixedJavaMemberLookupConfig quteProcessor_FixedJavaMemberLookupConfig0 = new QuteProcessor.FixedJavaMemberLookupConfig(compositeIndex0, predicate0, false);
      quteProcessor_FixedJavaMemberLookupConfig0.nextPart();
      assertFalse(quteProcessor_FixedJavaMemberLookupConfig0.declaredMembersOnly());
  }

  @Test(timeout = 4000)
  public void testExtractMatchTypeThrowsIllegalArgumentException()  throws Throwable  {
      HashSet<Type> hashSet0 = new HashSet<Type>(10);
      ClassType classType0 = ClassType.BOOLEAN_CLASS;
      WildcardType wildcardType0 = WildcardType.createLowerBound((Type) classType0);
      DotName dotName0 = wildcardType0.name();
      hashSet0.add(wildcardType0);
      Function<Type, Type> function0 = QuteProcessor.FIRST_PARAM_TYPE_EXTRACT_FUN;
      // Undeclared exception!
      try { 
        QuteProcessor.extractMatchType(hashSet0, dotName0, function0);
        fail("Expecting exception: IllegalArgumentException");
      
      } catch(IllegalArgumentException e) {
         //
         // Not a parameterized type!
         //
         verifyException("org.jboss.jandex.Type", e);
      }
  }

  @Test(timeout = 4000)
  public void testGetNameTakingNoArguments()  throws Throwable  {
      QuteProcessor.Code quteProcessor_Code0 = QuteProcessor.Code.INCORRECT_EXPRESSION;
      String string0 = quteProcessor_Code0.getName();
      assertEquals("BUILD_INCORRECT_EXPRESSION", string0);
  }

  @Test(timeout = 4000)
  public void testGetGeneratedGlobalClass()  throws Throwable  {
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      // Undeclared exception!
      try { 
        quteProcessor_ExistingValueResolvers0.getGeneratedGlobalClass((DotName) null);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"org.jboss.jandex.DotName.toString()\" because \"declaringClassName\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor$ExistingValueResolvers", e);
      }
  }

  @Test(timeout = 4000)
  public void testGetGeneratedClass()  throws Throwable  {
      QuteProcessor.ExistingValueResolvers quteProcessor_ExistingValueResolvers0 = new QuteProcessor.ExistingValueResolvers();
      // Undeclared exception!
      try { 
        quteProcessor_ExistingValueResolvers0.getGeneratedClass((MethodInfo) null);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"org.jboss.jandex.MethodInfo.declaringClass()\" because \"extensionMethod\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor$ExistingValueResolvers", e);
      }
  }

  @Test(timeout = 4000)
  public void testCreatesFirstPassJavaMemberLookupConfigTaking3ArgumentsAndCallsNextPart()  throws Throwable  {
      Predicate<AnnotationTarget> predicate0 = Predicate.isEqual(null);
      Boolean boolean0 = Boolean.FALSE;
      QuteProcessor.FirstPassJavaMemberLookupConfig quteProcessor_FirstPassJavaMemberLookupConfig0 = new QuteProcessor.FirstPassJavaMemberLookupConfig((QuteProcessor.JavaMemberLookupConfig) null, predicate0, boolean0);
      quteProcessor_FirstPassJavaMemberLookupConfig0.nextPart();
  }

  @Test(timeout = 4000)
  public void testReadTemplateContent()  throws Throwable  {
      File file0 = File.createTempFile(">CB2B?chR;(`fsL7", ">CB2B?chR;(`fsL7");
      Path path0 = file0.toPath();
      Charset charset0 = Charset.defaultCharset();
      String string0 = QuteProcessor.readTemplateContent(path0, charset0);
      assertEquals("", string0);
  }

  @Test(timeout = 4000)
  public void testTurnLocationIntoQualifier()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      QualifierRegistrarBuildItem qualifierRegistrarBuildItem0 = quteProcessor0.turnLocationIntoQualifier();
      assertNotNull(qualifierRegistrarBuildItem0);
  }

  @Test(timeout = 4000)
  public void testAdditionalBeans()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      AdditionalBeanBuildItem additionalBeanBuildItem0 = quteProcessor0.additionalBeans();
      assertFalse(additionalBeanBuildItem0.isRemovable());
  }

  @Test(timeout = 4000)
  public void testBeanDefiningAnnotations()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      List<BeanDefiningAnnotationBuildItem> list0 = quteProcessor0.beanDefiningAnnotations();
      assertFalse(list0.isEmpty());
  }

  @Test(timeout = 4000)
  public void testQuteDebuggerBean()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      AdditionalBeanBuildItem additionalBeanBuildItem0 = quteProcessor0.quteDebuggerBean();
      assertFalse(additionalBeanBuildItem0.isRemovable());
  }

  @Test(timeout = 4000)
  public void testAddSingletonToNamedRecords()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      AutoAddScopeBuildItem autoAddScopeBuildItem0 = quteProcessor0.addSingletonToNamedRecords();
      assertEquals("Found Java record annotated with @Named", autoAddScopeBuildItem0.getReason());
  }

  @Test(timeout = 4000)
  public void testValidateExpressions()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      Vector<TemplatesAnalysisBuildItem.TemplateAnalysis> vector0 = new Vector<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(vector0);
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      DotName dotName0 = DotName.INHERITED_NAME;
      Set<DotName> set0 = emptyIndex0.getSubpackages(dotName0);
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, set0);
      LinkedList<TemplateExtensionMethodBuildItem> linkedList0 = new LinkedList<TemplateExtensionMethodBuildItem>();
      Stack<TypeCheckExcludeBuildItem> stack0 = new Stack<TypeCheckExcludeBuildItem>();
      Class<IncorrectExpressionBuildItem> class0 = IncorrectExpressionBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<IncorrectExpressionBuildItem> buildProducerImpl0 = new BuildProducerImpl<IncorrectExpressionBuildItem>(class0, buildContext0);
      BuildProducer<ImplicitValueResolverBuildItem> buildProducer0 = (BuildProducer<ImplicitValueResolverBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<TemplateExpressionMatchesBuildItem> buildProducer1 = (BuildProducer<TemplateExpressionMatchesBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      Vector<BeanInfo> vector1 = new Vector<BeanInfo>();
      SynchronousQueue<InjectionPointInfo> synchronousQueue0 = new SynchronousQueue<InjectionPointInfo>();
      LinkedBlockingDeque<ObserverInfo> linkedBlockingDeque0 = new LinkedBlockingDeque<ObserverInfo>();
      BeanDeployment beanDeployment0 = mock(BeanDeployment.class, new ViolatedAssumptionAnswer());
      doReturn((BeanResolver) null).when(beanDeployment0).getBeanResolver();
      doReturn(vector1).when(beanDeployment0).getBeans();
      doReturn(synchronousQueue0).when(beanDeployment0).getInjectionPoints();
      doReturn(linkedBlockingDeque0).when(beanDeployment0).getObservers();
      SynthesisFinishedBuildItem synthesisFinishedBuildItem0 = new SynthesisFinishedBuildItem(beanDeployment0);
      Stack<CheckedTemplateBuildItem> stack1 = new Stack<CheckedTemplateBuildItem>();
      ArrayList<TemplateDataBuildItem> arrayList0 = new ArrayList<TemplateDataBuildItem>();
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      NativeConfig nativeConfig0 = mock(NativeConfig.class, new ViolatedAssumptionAnswer());
      doReturn(true).when(nativeConfig0).enabled();
      Vector<TemplateGlobalBuildItem> vector2 = new Vector<TemplateGlobalBuildItem>();
      quteProcessor0.validateExpressions(templatesAnalysisBuildItem0, beanArchiveIndexBuildItem0, linkedList0, stack0, buildProducerImpl0, buildProducer0, buildProducer1, synthesisFinishedBuildItem0, stack1, arrayList0, quteConfig0, nativeConfig0, vector2);
      assertTrue(stack0.empty());
  }

  @Test(timeout = 4000)
  public void testFeature()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      FeatureBuildItem featureBuildItem0 = quteProcessor0.feature();
      assertEquals("qute", featureBuildItem0.getName());
  }

  @Test(timeout = 4000)
  public void testInitializeGeneratedClassesThrowsNullPointerException()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      BeanContainerBuildItem beanContainerBuildItem0 = new BeanContainerBuildItem((BeanContainer) null);
      QuteRecorder quteRecorder0 = new QuteRecorder();
      Stack<GeneratedValueResolverBuildItem> stack0 = new Stack<GeneratedValueResolverBuildItem>();
      LinkedList<TemplateGlobalProviderBuildItem> linkedList0 = new LinkedList<TemplateGlobalProviderBuildItem>();
      // Undeclared exception!
      try { 
        quteProcessor0.initializeGeneratedClasses(beanContainerBuildItem0, quteRecorder0, stack0, linkedList0);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"io.quarkus.arc.ArcContainer.instance(java.lang.Class, java.lang.annotation.Annotation[])\" because the return value of \"io.quarkus.arc.Arc.container()\" is null
         //
         verifyException("io.quarkus.qute.runtime.QuteRecorder", e);
      }
  }

  @Test(timeout = 4000)
  public void testGenerateValueResolversThrowsNullPointerException()  throws Throwable  {
      QuteProcessor quteProcessor0 = new QuteProcessor();
      TreeSet<DotName> treeSet0 = new TreeSet<DotName>();
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      BeanArchiveIndexBuildItem beanArchiveIndexBuildItem0 = new BeanArchiveIndexBuildItem(emptyIndex0, emptyIndex0, treeSet0);
      QuteConfig quteConfig0 = mock(QuteConfig.class, new ViolatedAssumptionAnswer());
      Class<GeneratedClassBuildItem> class0 = GeneratedClassBuildItem.class;
      BuildContext buildContext0 = mock(BuildContext.class, new ViolatedAssumptionAnswer());
      BuildProducerImpl<GeneratedClassBuildItem> buildProducerImpl0 = new BuildProducerImpl<GeneratedClassBuildItem>(class0, buildContext0);
      BuildProducer<GeneratedResourceBuildItem> buildProducer0 = (BuildProducer<GeneratedResourceBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      String[] stringArray0 = new String[6];
      stringArray0[0] = "@TemplateData declared on %s is ignored: target %s it is not available in the index";
      stringArray0[1] = "@TemplateData declared on %s is ignored: target %s it is not available in the index";
      stringArray0[2] = "@TemplateData declared on %s is ignored: target %s it is not available in the index";
      stringArray0[3] = "i(,oE[Sd$N[ /";
      stringArray0[4] = "@TemplateData declared on %s is ignored: target %s it is not available in the index";
      stringArray0[5] = "@TemplateData declared on %s is ignored: target %s it is not available in the index";
      Path path0 = Path.of("@TemplateData declared on %s is ignored: target %s it is not available in the index", stringArray0);
      DirectoryPathTree directoryPathTree0 = new DirectoryPathTree(path0);
      ResolvedDependencyBuilder resolvedDependencyBuilder0 = ResolvedDependencyBuilder.newInstance();
      ResolvedArtifactDependency resolvedArtifactDependency0 = new ResolvedArtifactDependency(resolvedDependencyBuilder0);
      ApplicationArchiveImpl applicationArchiveImpl0 = new ApplicationArchiveImpl(emptyIndex0, directoryPathTree0, resolvedArtifactDependency0);
      LinkedHashSet<ApplicationArchive> linkedHashSet0 = new LinkedHashSet<ApplicationArchive>();
      ApplicationArchivesBuildItem applicationArchivesBuildItem0 = new ApplicationArchivesBuildItem(applicationArchiveImpl0, linkedHashSet0);
      LinkedList<TemplateExtensionMethodBuildItem> linkedList0 = new LinkedList<TemplateExtensionMethodBuildItem>();
      ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis> arrayList0 = new ArrayList<TemplatesAnalysisBuildItem.TemplateAnalysis>();
      TemplatesAnalysisBuildItem templatesAnalysisBuildItem0 = new TemplatesAnalysisBuildItem(arrayList0);
      LiveReloadBuildItem liveReloadBuildItem0 = new LiveReloadBuildItem();
      Set<String> set0 = liveReloadBuildItem0.getChangedResources();
      PanacheEntityClassesBuildItem panacheEntityClassesBuildItem0 = new PanacheEntityClassesBuildItem(set0);
      List<PanacheEntityClassesBuildItem> list0 = List.of(panacheEntityClassesBuildItem0, panacheEntityClassesBuildItem0, panacheEntityClassesBuildItem0, panacheEntityClassesBuildItem0);
      LinkedList<TemplateDataBuildItem> linkedList1 = new LinkedList<TemplateDataBuildItem>();
      Stack<TemplateGlobalBuildItem> stack0 = new Stack<TemplateGlobalBuildItem>();
      ArrayList<IncorrectExpressionBuildItem> arrayList1 = new ArrayList<IncorrectExpressionBuildItem>();
      CompletedApplicationClassPredicateBuildItem completedApplicationClassPredicateBuildItem0 = mock(CompletedApplicationClassPredicateBuildItem.class, new ViolatedAssumptionAnswer());
      BuildProducer<GeneratedValueResolverBuildItem> buildProducer1 = (BuildProducer<GeneratedValueResolverBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<ReflectiveClassBuildItem> buildProducer2 = (BuildProducer<ReflectiveClassBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      BuildProducer<TemplateGlobalProviderBuildItem> buildProducer3 = (BuildProducer<TemplateGlobalProviderBuildItem>) mock(BuildProducer.class, new ViolatedAssumptionAnswer());
      // Undeclared exception!
      try { 
        quteProcessor0.generateValueResolvers(quteConfig0, buildProducerImpl0, buildProducer0, beanArchiveIndexBuildItem0, applicationArchivesBuildItem0, linkedList0, (List<ImplicitValueResolverBuildItem>) null, templatesAnalysisBuildItem0, list0, linkedList1, stack0, arrayList1, liveReloadBuildItem0, completedApplicationClassPredicateBuildItem0, buildProducer1, buildProducer2, buildProducer3);
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"java.util.List.iterator()\" because \"implicitClasses\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor", e);
      }
  }

  @Test(timeout = 4000)
  public void testGetTypeParameters()  throws Throwable  {
      Class<String>[] classArray0 = (Class<String>[]) Array.newInstance(Class.class, 0);
      Index index0 = Index.of(classArray0);
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(index0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      // Undeclared exception!
      try { 
        quteProcessor_MatchResult0.getTypeParameters();
        fail("Expecting exception: NullPointerException");
      
      } catch(NullPointerException e) {
         //
         // Cannot invoke \"org.jboss.jandex.ClassInfo.typeParameters()\" because \"this.clazz\" is null
         //
         verifyException("io.quarkus.qute.deployment.QuteProcessor$MatchResult", e);
      }
  }

  @Test(timeout = 4000)
  public void testType()  throws Throwable  {
      EmptyIndex emptyIndex0 = EmptyIndex.INSTANCE;
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(emptyIndex0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      Type type0 = quteProcessor_MatchResult0.type();
      assertNull(type0);
  }

  @Test(timeout = 4000)
  public void testClearValues()  throws Throwable  {
      IndexView indexView0 = IndexView.empty();
      Types.AssignabilityCheck types_AssignabilityCheck0 = new Types.AssignabilityCheck(indexView0);
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult(types_AssignabilityCheck0);
      quteProcessor_MatchResult0.clearValues();
  }

  @Test(timeout = 4000)
  public void testClazz()  throws Throwable  {
      QuteProcessor.MatchResult quteProcessor_MatchResult0 = new QuteProcessor.MatchResult((Types.AssignabilityCheck) null);
      ClassInfo classInfo0 = quteProcessor_MatchResult0.clazz();
      assertNull(classInfo0);
  }
}
