function MakeComp(Board: Variant; X, Y, Rot: Double; Bottom: Boolean; Des: String): Variant;
var Comp, Pad, Body, Model: Variant;
begin
  Comp := PCBServer.PCBObjectFactory(eComponentObject, eNoDimension, eCreate_Default);
  PCBServer.PreProcess;
  Comp.X := MilsToCoord(X);
  Comp.Y := MilsToCoord(Y);
  if Bottom then Comp.Layer := eBottomLayer else Comp.Layer := eTopLayer;
  Comp.Rotation := Rot;
  Comp.Pattern := 'TESTFP';
  Comp.Name.Text := Des;
  Comp.Comment.Text := 'Comment' + Des;
  Board.AddPCBObject(Comp);
  L(Des + ' after add: X=' + FloatToStr(CoordToMils(Comp.X)) + ' Y=' + FloatToStr(CoordToMils(Comp.Y)) + ' rot=' + FloatToStr(Comp.Rotation) + ' layer=' + IntToStr(Comp.Layer));

  Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
  Pad.X := MilsToCoord(X + 100);
  Pad.Y := MilsToCoord(Y);
  Pad.TopXSize := MilsToCoord(60);
  Pad.TopYSize := MilsToCoord(30);
  Pad.TopShape := eRectangular;
  if Bottom then Pad.Layer := eBottomLayer else Pad.Layer := eTopLayer;
  Pad.HoleSize := 0;
  Pad.Name := '1';
  Pad.Rotation := Rot;
  Comp.AddPCBObject(Pad);
  Board.AddPCBObject(Pad);
  L(Des + ' pad: X=' + FloatToStr(CoordToMils(Pad.X)) + ' Y=' + FloatToStr(CoordToMils(Pad.Y)) + ' rot=' + FloatToStr(Pad.Rotation));

  Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  Model := Body.ModelFactory_FromFilename('%ROOT%\testdata\models\model2.stp', False);
  if Model = Nil then L('model nil') else L('model ok');
  Body.SetState_FromModel;
  Model.SetState(0, 0, Rot, 0);
  Body.Model := Model;
  if Bottom then Body.Layer := eBottomLayer else Body.Layer := eTopLayer;
  Comp.AddPCBObject(Body);
  Board.AddPCBObject(Body);
  L(Des + ' body: overall=' + FloatToStr(CoordToMils(Body.OverallHeight)) + ' bbox L=' + FloatToStr(CoordToMils(Body.BoundingRectangle.Left)));
  PCBServer.PostProcess;
  Result := Comp;
end;

procedure Body;
var
  Doc, Board, C: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  C := MakeComp(Board, 3000, 3000, 0, False, 'U1');
  C := MakeComp(Board, 4000, 3000, 90, False, 'U2');
  C := MakeComp(Board, 3000, 4000, 0, True, 'U3');
  C := MakeComp(Board, 4000, 4000, 90, True, 'U4');
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p05.PcbDoc');
  Doc.DoFileSave('');
  L('saved');
end;
